#!/usr/bin/env python3
"""Load KOL/leaderboard wallet intel into ``wallet_v2_8b`` (Qwen3-Embedding-8B, 4096-d).

WHY THIS EXISTS SEPARATELY FROM ingest_wallet_labels.py
------------------------------------------------------
That script targets ``wallet_v2``, which is the RETIRED 2560-dim qwen3-embedding-8b
lane. Both collections exist and currently hold the same point count, so a load
into the wrong one does not error — it quietly builds a second copy of the entity
corpus in a lane nothing queries. On an intel platform that is the worst
failure available: the data is not missing, it is INVISIBLE, and the symptom
reads as "we have no wallet intel" rather than "we loaded it into the retired
lane".

So this loader is explicit about the target and refuses to guess.

PROVENANCE IS NOT OPTIONAL
--------------------------
Every row carries ``sources`` (list) and ``confidence``. A wallet that appears
in one leaderboard dump is not the same claim as one that appears in a live
leaderboard AND a GMGN category dump, and an entity corpus that cannot say
where a label came from is how a platform ends up confidently wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import uuid

import httpx
from qdrant_client import QdrantClient, models

LOG = logging.getLogger("kol-wallet-load")

# Hard-coded on purpose: this loader must never infer the collection from env,
# because the env still names the retired 4B lane.
COLLECTION = "wallet_v2_8b"
DENSE = "dense"          # named dense vector for the 8B lane
SPARSE = "bm25"
MODEL = "Qwen/Qwen3-Embedding-8B"
DIM = 4096
NAMESPACE = uuid.UUID("6f1b0c9e-4a7d-4c2e-9f3a-0d2b8e5c7a41")  # stable -> idempotent ids

SOLANA = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")

# How much we believe a label, derived from how many independent sources
# carried it. Deliberately coarse: a number we can defend beats a precision
# we cannot.
CONFIDENCE = {1: "low", 2: "medium"}


def classify(addr: str) -> str:
    """Return 'solana', 'evm' or 'unknown' for an address by shape."""
    if SOLANA.match(addr):
        return "solana"
    if EVM.match(addr):
        return "evm"
    return "unknown"


# DeepInfra primary, SiliconFlow backup. SAME model and SAME dimensions on both,
# so a failover mid-run cannot silently produce a mixed-dimension corpus.
#
# SiliconFlow is .com. The .cn host rejects every credential with
# "Token is invalid", which reads exactly like a bad key and cost a while.
LANES = (
    ("deepinfra", "https://api.deepinfra.com/v1", "DEEPINFRA_API_KEY"),
    ("siliconflow", "https://api.siliconflow.com/v1", "SILICONFLOW_API_KEY"),
)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed via DeepInfra, falling back to SiliconFlow on any failure.

    Raises only if every configured lane fails, so a partial outage degrades to
    a slower load rather than a corrupt one.
    """
    errors = []
    for name, url, env in LANES:
        key = os.environ.get(env, "")
        if not key:
            continue
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{url}/embeddings",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": MODEL, "input": texts, "dimensions": DIM},
                )
                r.raise_for_status()
                out = r.json()["data"]
            by = {d["index"]: d["embedding"] for d in out}
            if name != LANES[0][0]:
                LOG.warning("primary lane failed earlier; %s served this batch", name)
            return [by[i] for i in range(len(texts))]
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__} {exc}"[:120])
    raise RuntimeError("all embed lanes failed: " + " | ".join(errors))


def doc_id(addr: str) -> str:
    """Stable UUIDv5 per address, so a re-run upserts instead of duplicating."""
    return str(uuid.uuid5(NAMESPACE, addr.lower()))


def build_payload(row: dict, kind: str) -> dict:
    """Render one wallet as a document with its sources and implied confidence."""
    addr = row["address"]
    srcs = row.get("sources", [])
    name = (row.get("name") or "").strip()
    handle = (row.get("handle") or "").strip()
    bits = [f"Wallet {addr}."]
    if name:
        bits.append(f"Named {name}.")
    if handle:
        bits.append(f"Handle @{handle}.")
    bits.append(f"Tracked {kind} on {', '.join(srcs)}.")
    return {
        "address": addr,
        "chain": kind,
        "kind": kind,
        "entity_type": "tracked_wallet",
        "label_type": "kol",
        "label_subtype": "leaderboard",
        "name": name,
        "handle": handle,
        "twitter": (row.get("extra") or {}).get("twitter", ""),
        "sources": srcs,
        "provenance": {"sources": srcs, "loaded_from": "kol-intel", "loaded_at": TODAY},
        "confidence": CONFIDENCE.get(len(srcs), "medium"),
        "source_count": len(srcs),
        "content": " ".join(bits),
    }


TODAY = __import__("datetime").date.today().isoformat()


def _read_json(path: str) -> list[dict]:
    """Read the merged wallet file.

    Deliberately a sync helper called before the async work: opening a file with
    blocking I/O inside a coroutine blocks the loop (ASYNC230), and a thread
    pool for a single small read is not worth it.
    """
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


async def main() -> int:
    """Load the merged KOL file into wallet_v2_8b, refusing a dimension mismatch."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="/root/data/kol-intel/kol-wallets-merged.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=64)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    rows = _read_json(args.source)
    if args.limit:
        rows = rows[: args.limit]

    keep = [r for r in rows if classify(r["address"]) != "unknown"]
    dropped = len(rows) - len(keep)
    LOG.info("source rows      : %d", len(rows))
    LOG.info("dropped (bad addr): %d", dropped)
    LOG.info("chain split      : %s", dict(
        (c, sum(1 for r in keep if classify(r["address"]) == c))
        for c in ("solana", "evm")))

    if args.dry_run:
        LOG.info("dry run — collection %s not touched", COLLECTION)
        return 0

    if not any(os.environ.get(e) for _, _, e in LANES):
        LOG.error("no embed lane has a key: need DEEPINFRA_API_KEY or SILICONFLOW_API_KEY")
        return 1

    qc = QdrantClient(url=os.environ.get("QDRANT_URL", "http://127.0.0.1:6334"))
    # qdrant reports an unnamed dense vector as a bare VectorParams, not a
    # dict, and names it "" — handle both shapes instead of assuming "dense".
    live = qc.get_collection(COLLECTION).config.params.vectors
    if live is None:
        LOG.error("%s reports no vector config", COLLECTION)
        return 1
    have = {k: v.size for k, v in live.items()} if isinstance(live, dict) else {"": live.size}
    LOG.info("target collection: %s  vectors=%s", COLLECTION, have)
    if DIM not in have.values():
        LOG.error("%s holds %s-dim vectors; this loader emits %s. Refusing to write 8B "
                  "vectors into a mismatched lane.", COLLECTION, list(have.values()), DIM)
        return 1
    dense_name = next(k for k, v in have.items() if v == DIM)

    for i in range(0, len(keep), args.batch):
        chunk = keep[i : i + args.batch]
        vecs = await embed_batch(
            [build_payload(r, classify(r["address"]))["content"] for r in chunk]
        )
        qc.upsert(
            collection_name=COLLECTION,
            points=[
                models.PointStruct(
                    id=doc_id(r["address"]),
                    vector={dense_name: v},
                    payload=build_payload(r, classify(r["address"])),
                )
                for r, v in zip(chunk, vecs, strict=True)
            ],
        )
        LOG.info("upserted %d/%d", i + len(chunk), len(keep))

    LOG.info("done: %d wallets in %s at %s dims", len(keep), COLLECTION, DIM)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
