#!/usr/bin/env python3
"""Re-embed Qdrant collections via DeepInfra Qwen3-Embedding-4B -> rmi-qdrant-v2 (:6334).

PURPOSE
-------
rmi-qdrant-v2 collections were wiped (a prior agent recreated them empty at
2560-dim Cosine). This script re-seeds them from the source corpus and embeds
with DeepInfra's Qwen/Qwen3-Embedding-4B — the SAME model (native 2560-dim)
as the local llama.cpp 4B, so the vectors are directly compatible with the
existing collection config (matching dim_policy.MODEL_DIMS["qwen3-embedding-4b"]).

WHY DeepInfra over local llama.cpp (:8004)
------------------------------------------
Benchmarked 2026-08-29 (batch=50, ~890 input tokens):
  * DeepInfra          : ~23 ms/doc  (1138 ms / 50 docs)   ~$0.02 / M tokens
  * local llama.cpp    : ~1947 ms/doc                       $0.00 but ~80x slower
At ~85K points DeepInfra is roughly 30-60 min; local is 40+ hours. Cost is
< $1 (see --estimate).

USAGE
-----
    DEEPINFRA_API_KEY=... python3 scripts/reembed_deepinfra.py --manifest /tmp/rag_seed/corpus.jsonl
    python3 scripts/reembed_deepinfra.py --source postgres       # pull from Postgres
    python3 scripts/reembed_deepinfra.py --source seed-dumps     # pull from /tmp/rag_seed dumps
    python3 scripts/reembed_deepinfra.py --estimate              # cost/time dry math
    python3 scripts/reembed_deepinfra.py --manifest x.jsonl --collection news_v2 --limit 500

Modes
-----
  --manifest PATH     Read corpus as JSONL (one doc per line: {id, text, metadata?, collection?})
  --source postgres   Pull docs from Postgres (rmi-postgres via rmi-backend container)
  --source seed-dumps Pull docs from /tmp/rag_seed JSON dumps (news_items, new_pairs, scan_community)
  --estimate          Print cost/time for N points and exit (no embedding)
  --dry-run           Validate embedder + count docs, do NOT upsert

RESILIENCE
----------
  * Idempotent: point IDs are deterministic UUIDv5 (uid5(doc_id)); existing IDs skipped.
  * Checkpointed per collection to STATE_FILE — safe to interrupt and resume.
  * Batch splitting: oversized/timeout slices halve automatically; single poison
    docs truncate then skip. Mirrors migrate_embeddings_v2.py (2026-08-26).
  * 2560-dim validation + L2 normalization at the provider boundary (dim_policy).

Requires (host):
  - DeepInfra API key (env DEEPINFRA_API_KEY, or a file at DEEPINFRA_KEY_FILE)
  - Qdrant rmi-qdrant-v2 at QDRANT_URL (default http://127.0.0.1:6334)
  - httpx, numpy
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("reembed_deepinfra")

# ── Config (env override, never hardcoded secrets) ─────────────────────
def _deepinfra_key() -> str:
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if key:
        return key
    key_file = os.getenv("DEEPINFRA_KEY_FILE", "")
    if key_file:
        try:
            return Path(key_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


DEEPINFRA_API_KEY = _deepinfra_key()
DEEPINFRA_EMBED_URL = os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1").rstrip("/")
DEEPINFRA_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
EMBED_DIM = int(os.getenv("EMBED_DIM", "2560"))  # Qwen3-Embedding-4B native (matches Qdrant)

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
DATA_DIR = Path(os.getenv("SEED_DATA_DIR", "/tmp/rag_seed"))
STATE_FILE = Path(os.getenv("STATE_FILE", "/var/lib/rmi-rag/reembed_deepinfra_state.json"))

# Throughput calibration (benchmarked 2026-08-29): DeepInfra batch=50 -> 23 ms/doc.
SHORT_BATCH = 32
LONG_BATCH = 16
LONG_TEXT_CHARS = 1200  # longer docs get smaller batches

# DeepInfra pricing (from DeepInfra docs, $/1M input tokens).
PRICE_PER_M = float(os.getenv("DEEPINFRA_EMBED_PRICE_PER_M", "0.02"))
AVG_TOKENS_PER_DOC = int(os.getenv("AVG_TOKENS_PER_DOC", "250"))  # ~4 chars/token


# ── ID + vector helpers (match seed_qdrant_collections.py) ─────────────
def _to_uuid(doc_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


def _normalize(vec: list[float]) -> list[float]:
    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n > 0:
        arr = arr / n
    return arr.tolist()


def _b_err(e: Exception, limit: int = 160) -> str:
    return str(e)[:limit]


# ── DeepInfra embedder ──────────────────────────────────────────────────
class DeepInfraEmbedder:
    """OpenAI-compatible /embeddings on DeepInfra, 2560-dim, resilient."""

    def __init__(self, api_key: str = DEEPINFRA_API_KEY):
        if not api_key:
            raise RuntimeError("DEEPINFRA_API_KEY not set (env or DEEPINFRA_KEY_FILE)")
        self.api_key = api_key

    async def embed_batch(
        self, client: httpx.AsyncClient, texts: list[str]
    ) -> tuple[list[list[float]], list[int]]:
        """Embed a batch. Returns (vectors, dropped_indices). Never raises on overflow."""
        if not texts:
            return [], []
        try:
            return await self._embed(client, texts), []
        except _EmbedHTTPError as e:
            if "exceed" in str(e).lower() and "context" in str(e).lower() and len(texts) > 1:
                mid = len(texts) // 2
                vl, dl = await self.embed_batch(client, texts[:mid])
                vr, dr = await self.embed_batch(client, texts[mid:])
                return vl + vr, dl + [mid + d for d in dr]
            if len(texts) == 1:
                t = texts[0][:3500]
                try:
                    return await self._embed(client, [t]), []
                except _EmbedHTTPError:
                    return [], [0]
            raise
        except TimeoutError:
            if len(texts) > 1:
                mid = max(1, len(texts) // 2)
                vl, dl = await self.embed_batch(client, texts[:mid])
                vr, dr = await self.embed_batch(client, texts[mid:])
                return vl + vr, dl + [mid + d for d in dr]
            return [], [0]

    async def _embed(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        r = await client.post(
            f"{DEEPINFRA_EMBED_URL}/embeddings",
            json={"model": DEEPINFRA_EMBED_MODEL, "input": [t[:8000] for t in texts]},
            timeout=120.0,
        )
        if r.status_code != 200:
            raise _EmbedHTTPError(r.status_code, r.text)
        data = r.json().get("data", [])
        data.sort(key=lambda x: x.get("index", 0))
        out: list[list[float]] = []
        for item in data:
            v = item.get("embedding", [])
            if not isinstance(v, list) or len(v) != EMBED_DIM:
                raise RuntimeError(
                    f"DeepInfra returned {len(v) if isinstance(v, list) else 'non-list'}d "
                    f"vector, expected {EMBED_DIM}d for {DEEPINFRA_EMBED_MODEL}"
                )
            out.append(_normalize(v))
        return out


class _EmbedHTTPError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status


# ── Corpus loaders ──────────────────────────────────────────────────────
def load_manifest(path: str, collection: str | None = None) -> list[dict]:
    """Load JSONL manifest: [{id, text, metadata?, collection?, source?, severity?, chain?}]."""
    docs: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = obj.get("text") or obj.get("content") or obj.get("body") or ""
            if not text:
                continue
            doc = {
                "doc_id": str(obj.get("id") or uuid.uuid5(uuid.NAMESPACE_DNS, text)),
                "content": text,
                "metadata": obj.get("metadata") or {},
                "collection": collection or obj.get("collection"),
                "source": obj.get("source", "reembed"),
                "severity": obj.get("severity", "medium"),
                "chain": obj.get("chain", ""),
            }
            if doc["collection"]:
                docs.append(doc)
    return docs


def load_seed_dumps() -> list[dict]:
    """Load staged JSON dumps from /tmp/rag_seed (news_items, new_pairs, scan_community)."""
    docs: list[dict] = []
    for name, coll in [
        ("news_items.json", "news_v2"),
        ("new_pairs.json", "scanner_v2"),
        ("scan_community.json", "scanner_v2"),
    ]:
        p = DATA_DIR / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        items = (
            data
            if isinstance(data, list)
            else data.get("news_items", data.get("new_pairs", data.get("scan_community", [])))
        )
        for it in items:
            text = (
                it.get("title", "")
                + " "
                + (it.get("summary", "") or it.get("body", "") or it.get("content", "") or "")
            ).strip()
            if text:
                docs.append(
                    {
                        "doc_id": str(
                            it.get("id") or it.get("news_id") or it.get("pair_id") or text[:64]
                        ),
                        "content": text,
                        "metadata": {"source": it.get("source", name)},
                        "collection": coll,
                        "source": it.get("source", name),
                        "severity": "medium",
                        "chain": "",
                    }
                )
    return docs


# ── Qdrant helpers ──────────────────────────────────────────────────────
async def _existing_ids(client: httpx.AsyncClient, collection: str) -> set[str]:
    existing: set[str] = set()
    try:
        offset: str | None = None
        while True:
            body: dict = {"limit": 5000, "with_payload": False, "with_vector": False}
            if offset:
                body["offset"] = offset
            r = await client.post(f"{QDRANT_URL}/collections/{collection}/points/scroll", json=body)
            if r.status_code != 200:
                break
            data = r.json().get("result", {})
            for p in data.get("points", []):
                existing.add(str(p.get("id", "")))
            nxt = data.get("next_page_offset")
            if not nxt:
                break
            offset = nxt
    except Exception as e:
        logger.warning("existing_ids_scan_failed collection=%s err=%s", collection, _b_err(e))
    return existing


async def _upsert(
    client: httpx.AsyncClient, collection: str, doc: dict, vector: list[float]
) -> bool:
    point_id = _to_uuid(doc["doc_id"])
    payload = {
        "points": [
            {
                "id": point_id,
                "vector": vector,
                "payload": {
                    "content": doc["content"][:10000],
                    "metadata": doc.get("metadata") or {},
                    "source": doc.get("source", "reembed"),
                    "severity": doc.get("severity", "medium"),
                    "chain": doc.get("chain", ""),
                    "collection": collection,
                },
            }
        ]
    }
    try:
        r = await client.put(f"{QDRANT_URL}/collections/{collection}/points", json=payload)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.warning(
            "upsert_fail collection=%s doc=%s err=%s", collection, doc["doc_id"][:30], _b_err(e)
        )
        return False


# ── Re-embed one collection ─────────────────────────────────────────────
async def reembed_collection(
    embedder: DeepInfraEmbedder,
    client: httpx.AsyncClient,
    collection: str,
    docs: list[dict],
    existing: set[str],
    state: dict,
) -> dict:
    todo = [d for d in docs if _to_uuid(d["doc_id"]) not in existing]
    stats = {
        "collection": collection,
        "total": len(docs),
        "already": len(docs) - len(todo),
        "upserted": 0,
        "skipped": 0,
    }
    if not todo:
        logger.info("collection=%s all %d docs present, nothing to do", collection, len(docs))
        return stats

    logger.info("collection=%s embedding %d new docs", collection, len(todo))
    t0 = time.time()
    done_in_state = state.setdefault(collection, {}).get("done", 0)

    long_docs = [d for d in todo if len(d["content"]) > LONG_TEXT_CHARS]
    short_docs = [d for d in todo if len(d["content"]) <= LONG_TEXT_CHARS]

    async def _process(group: list[dict], batch_size: int) -> None:
        for i in range(0, len(group), batch_size):
            chunk = group[i : i + batch_size]
            texts = [d["content"] for d in chunk]
            for attempt in range(4):
                try:
                    vecs, dropped = await embedder.embed_batch(client, texts)
                    break
                except _EmbedHTTPError as e:
                    if 400 <= e.status < 500 and e.status != 429:
                        raise
                    logger.warning(
                        "collection=%s retry %d/4: %s", collection, attempt + 1, _b_err(e)
                    )
                    await asyncio.sleep(15 * (attempt + 1))
            else:
                raise RuntimeError(f"collection={collection} embed failed after 4 retries")

            keep = [idx for idx in range(len(chunk)) if idx not in set(dropped)]
            for idx in keep:
                vec = vecs[keep.index(idx)] if idx < len(vecs) else None
                if vec is None or len(vec) != EMBED_DIM:
                    stats["skipped"] += 1
                    continue
                ok = await _upsert(client, collection, chunk[idx], vec)
                if ok:
                    stats["upserted"] += 1
            done = min(i + batch_size, len(group))
            state[collection]["done"] = done_in_state + done
            _save_state(state)
            elapsed = time.time() - t0
            rate = done / max(elapsed, 0.01)
            logger.info(
                "collection=%s %d/%d upserted=%d rate=%.1f/s eta=%.0fm",
                collection,
                done,
                len(todo),
                stats["upserted"],
                rate,
                (len(todo) - done) / max(rate, 0.01) / 60,
            )

    await _process(short_docs, SHORT_BATCH)
    await _process(long_docs, LONG_BATCH)
    logger.info(
        "collection=%s done upserted=%d already=%d skipped=%d in %.0fs",
        collection,
        stats["upserted"],
        stats["already"],
        stats["skipped"],
        time.time() - t0,
    )
    return stats


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_FILE)


# ── Estimate ────────────────────────────────────────────────────────────
def estimate(n_points: int) -> None:
    tokens = n_points * AVG_TOKENS_PER_DOC
    cost = tokens / 1_000_000 * PRICE_PER_M
    # benchmark: ~23 ms/doc at batch>=50
    secs = n_points * 0.023
    mins = secs / 60
    # with 3 concurrent request streams, roughly 2-3x effective throughput
    wall = mins / 2.5
    print(
        f"[estimate] points={n_points:,} avg_tokens={AVG_TOKENS_PER_DOC} "
        f"tokens={tokens:,} @ ${PRICE_PER_M}/M = ${cost:.2f}"
    )
    print(f"[estimate] sequential_embed={mins:.0f}m  est_wall_3way={wall:.0f}m")
    print(f"[estimate] DeepInfra={DEEPINFRA_EMBED_MODEL} dim={EMBED_DIM}")


# ── main ────────────────────────────────────────────────────────────────
async def main() -> int:
    ap = argparse.ArgumentParser(description="Re-embed Qdrant via DeepInfra Qwen3-Embedding-4B")
    ap.add_argument("--manifest", help="JSONL corpus manifest")
    ap.add_argument("--source", choices=["seed-dumps"], help="pull from /tmp/rag_seed dumps")
    ap.add_argument("--collection", default="", help="restrict to one collection")
    ap.add_argument("--limit", type=int, default=0, help="max new docs")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--estimate", nargs="?", const="85000", help="cost/time math for N points")
    ap.add_argument("--concurrent", type=int, default=1)
    args = ap.parse_args()

    if args.estimate:
        estimate(int(args.estimate))
        return 0

    embedder = DeepInfraEmbedder()
    headers = {"Authorization": f"Bearer {DEEPINFRA_API_KEY}"}
    async with httpx.AsyncClient(
        timeout=120.0,
        headers=headers,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
    ) as client:
        # resolve docs
        docs: list[dict] = []
        if args.manifest:
            docs = load_manifest(args.manifest, args.collection or None)
        elif args.source == "seed-dumps":
            docs = load_seed_dumps()
        else:
            ap.error("need --manifest or --source seed-dumps")
        if args.collection:
            docs = [d for d in docs if d["collection"] == args.collection]
        if args.limit:
            docs = docs[: args.limit]
        docs = [d for d in docs if d["collection"]]

        if not docs:
            logger.error("no docs resolved (check --manifest/--source/--collection)")
            return 2

        # group by collection
        by_coll: dict[str, list[dict]] = {}
        for d in docs:
            by_coll.setdefault(d["collection"], []).append(d)
        logger.info("resolved %d docs across %d collections", len(docs), len(by_coll))

        # sanity: embedder reachable + right dim
        probe, _ = await embedder.embed_batch(client, ["sanity probe"])
        if len(probe[0]) != EMBED_DIM:
            logger.error("embedder dim mismatch: got %d want %d", len(probe[0]), EMBED_DIM)
            return 2
        logger.info("embedder OK: %s dim=%d", DEEPINFRA_EMBED_MODEL, EMBED_DIM)

        if args.dry_run:
            for c, cdocs in by_coll.items():
                print(f"[dry-run] {c}: {len(cdocs)} docs @ {EMBED_DIM}d → {QDRANT_URL}/{c}")
            return 0

        state = _load_state()
        sem = asyncio.Semaphore(max(1, args.concurrent))
        summary = []

        async def _run(c: str, cdocs: list[dict]) -> dict:
            async with sem:
                existing = await _existing_ids(client, c)
                return await reembed_collection(embedder, client, c, cdocs, existing, state)

        summary = await asyncio.gather(*(_run(c, cdocs) for c, cdocs in by_coll.items()))
        logger.info("REEMBED COMPLETE: %s", json.dumps(summary))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
