"""Re-embed legacy 1024-dim Qdrant collections into rmi-qdrant-v2 at 2560-dim.

Reads points (payload only) from legacy rmi-qdrant (:6333), re-embeds the
stored text with Qwen3-Embedding-4B (llama.cpp :8004), recomputes BM25
sparse vectors where the target collection supports them, and upserts into
rmi-qdrant-v2 (:6334) keeping the same point UUIDs and payloads.

Checkpointed per collection — safe to interrupt; re-running resumes where
it left off. Hot collections first.

Usage:
    python3 scripts/migrate_embeddings_v2.py                # everything
    python3 scripts/migrate_embeddings_v2.py --collections news_v2,scam_intel_v2
    python3 scripts/migrate_embeddings_v2.py --batch 64 --limit 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("reembed")

LEGACY = os.getenv("LEGACY_QDRANT", "http://127.0.0.1:6333")
V2 = os.getenv("V2_QDRANT", "http://127.0.0.1:6334")
EMBEDDER = os.getenv("EMBEDDER_URL", "http://172.22.0.1:8004")
STATE_FILE = Path(os.getenv("STATE_FILE", "/var/lib/rmi-rag/reembed_state.json"))

# Field priority mirroring what ingestion embeds (content first).
TEXT_FIELDS = ("content", "text", "chunk_text", "title", "summary", "description", "name")

# Large collections first so progress is visible early.
HOT_FIRST = (
    "codebase_reference",
    "defi_exploits",
    "threat_intel_v2",
    "wallet_v2",
    "scam_patterns",
    "known_scams",
    "rgm_wallets",
    "news_v2",
    "scam_intel_v2",
)

INSTRUCT = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: {}"


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


def _extract_text(payload: dict) -> str:
    for f in TEXT_FIELDS:
        v = payload.get(f) or (payload.get("metadata") or {}).get(f)
        if isinstance(v, str) and v.strip():
            return v[:4000]
    return ""


async def _supports_sparse(client: httpx.AsyncClient, collection: str) -> bool:
    r = await client.get(f"{V2}/collections/{collection}")
    if r.status_code != 200:
        return False
    vectors = r.json()["result"]["config"]["params"].get("vectors", {})
    return isinstance(vectors, dict) and "bm25" in vectors


async def _embed_batch(client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    """Embed a batch of documents (no instruction prefix — docs are raw)."""
    resp = await client.post(f"{EMBEDDER}/v1/embeddings", json={"input": texts})
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda x: x["index"])
    return [d["embedding"] for d in data]


async def _embed_query(client: httpx.AsyncClient, query: str) -> list[float]:
    resp = await client.post(f"{EMBEDDER}/v1/embeddings", json={"input": [INSTRUCT.format(query)]})
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


async def _sparse_for(text: str) -> dict | None:
    """Reuse rugbrain's fastembed BM25 sparse (mirrors qdrant_store.insert)."""
    try:
        from rugbrain.qdrant_store import _embed_sparse  # type: ignore

        sparse = await _embed_sparse(text)
        if sparse is not None:
            return {"indices": sparse[0], "values": sparse[1]}
    except Exception as e:
        logger.warning("sparse_embed_failed: %s", e)
    return None


async def migrate_collection(
    client: httpx.AsyncClient,
    name: str,
    state: dict,
    batch: int,
    limit: int,
    skip_sparse: bool = False,
    concurrent: int = 1,
) -> dict:
    """Re-embed one collection. Sequential per collection; collections run
    concurrently via asyncio.gather in main() (matches llama.cpp --parallel)."""
    info = (await client.get(f"{LEGACY}/collections/{name}")).json()["result"]
    total = info.get("points_count", 0)
    done = state.get(name, {}).get("done", 0)
    if total == 0 or done >= total:
        return {"name": name, "total": total, "done": total, "skipped": True}

    supports_sparse = await _supports_sparse(client, name)
    stats = {"name": name, "total": total, "done": done, "upserted": 0, "failed": 0, "no_text": 0}

    t0 = time.time()
    last_log = t0
    cursor: str | None = state.get(name, {}).get("cursor")
    offset = done
    while offset < total:
        r = await client.post(
            f"{LEGACY}/collections/{name}/points/scroll",
            json={"limit": batch, "offset": cursor, "with_payload": True, "with_vector": False},
        )
        r.raise_for_status()
        result = r.json()["result"]
        points = result.get("points", [])
        if not points:
            break
        cursor = result.get("next_page_offset")

        texts, payloads, ids = [], [], []
        for p in points:
            payload = p.get("payload") or {}
            text = _extract_text(payload)
            if not text:
                stats["no_text"] += 1
                continue
            texts.append(text)
            payloads.append(payload)
            ids.append(p["id"])

        if not texts:
            offset += len(points)
            continue

        # Embed the entire batch in one call (embedder handles 128+ texts fine).
        # The embedder's internal batch_size=2048 handles this efficiently.
        while True:
            try:
                vecs = await _embed_batch(client, texts)
                points_to_upsert = []
                for i, vec in enumerate(vecs):
                    point = {"id": ids[i], "payload": payloads[i]}
                    vector: dict = {"dense": vec}
                    if supports_sparse and not skip_sparse:
                        sp = await _sparse_for(texts[i])
                        if sp is not None:
                            vector["bm25"] = sp
                    point["vector"] = vector
                    points_to_upsert.append(point)
                # Batch upsert with retry
                for attempt in range(3):
                    try:
                        put = await client.put(
                            f"{V2}/collections/{name}/points",
                            json={"points": points_to_upsert},
                            timeout=120.0,
                        )
                        put.raise_for_status()
                        break
                    except Exception as e:
                        if attempt < 2:
                            logger.warning(
                                "upsert_retry %d/3 %s offset=%d: %s", attempt + 1, name, offset, e
                            )
                            await asyncio.sleep(5 * (attempt + 1))
                        else:
                            raise
                stats["upserted"] += len(points_to_upsert)
                offset += len(points)
                break  # success — move to next scroll
            except Exception as e:
                logger.error("batch_retry %s offset=%d: %s — retrying in 15s", name, offset, e)
                await asyncio.sleep(15)

        state.setdefault(name, {})["done"] = offset
        state[name]["cursor"] = cursor
        now = time.time()
        if now - last_log >= 60:
            last_log = now
            _save_state(state)
            rate = offset / max(now - t0, 1)
            eta = (total - offset) / max(rate, 0.01)
            logger.info(
                "%s %d/%d (%.0f%%) upserted=%d no_text=%d failed=%d rate=%.1f/s eta=%.0fm",
                name,
                offset,
                total,
                100 * offset / total,
                stats["upserted"],
                stats["no_text"],
                stats["failed"],
                rate,
                eta / 60,
            )
        if limit and offset >= limit:
            logger.info("%s: hit --limit %d, stopping", name, limit)
            break

    logger.info("collection done: %s %d/%d in %.0fs", name, offset, total, time.time() - t0)
    return stats


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collections", default="", help="comma-separated subset")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument(
        "--concurrent",
        type=int,
        default=3,
        help="concurrent collections (scroll/upsert overlap while embed is sequential)",
    )
    ap.add_argument("--limit", type=int, default=0, help="max points per collection")
    ap.add_argument(
        "--skip-sparse", action="store_true", help="skip BM25 sparse vector computation (faster)"
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # max_keepalive_connections=0 → fresh TCP connection per request.
    # Qdrant (actix) closes idle keep-alive sockets (~75s) while our batches
    # take 50+s per embed — reusing a half-closed socket causes CLOSE-WAIT
    # hangs that freeze the whole migration. Fresh conns cost ~1ms each here.
    import httpx as _httpx

    async with _httpx.AsyncClient(
        timeout=900.0,
        limits=_httpx.Limits(max_keepalive_connections=0, max_connections=50),
    ) as client:
        cols = [
            c["name"]
            for c in (await client.get(f"{LEGACY}/collections")).json()["result"]["collections"]
        ]
        if args.collections:
            subset = {c.strip() for c in args.collections.split(",") if c.strip()}
            cols = [c for c in cols if c in subset]
        cols.sort(key=lambda c: (0 if c in HOT_FIRST else 1, c))

        state = _load_state()
        if args.dry_run:
            for c in cols:
                total = (
                    (await client.get(f"{LEGACY}/collections/{c}"))
                    .json()["result"]
                    .get("points_count", 0)
                )
                done = state.get(c, {}).get("done", 0)
                print(f"[dry-run] {c}: total={total} done={done} remaining={max(total - done, 0)}")
            return 0

        # sanity: embedder reachable
        try:
            probe = await _embed_batch(client, ["sanity probe"])
            if len(probe[0]) != 2560:
                logger.error("embedder dim != 2560 (got %d) — aborting", len(probe[0]))
                return 2
        except Exception as e:
            logger.error("embedder unreachable: %s", e)
            return 2

        # Run collections concurrently (semaphore caps in-flight embed
        # requests at llama.cpp --parallel slots). State file is written
        # per-collection, so parallel collections never clobber each other.
        sem = asyncio.Semaphore(max(1, args.concurrent))

        async def _run(c: str) -> dict:
            async with sem:
                return await migrate_collection(
                    client, c, state, args.batch, args.limit, args.skip_sparse, args.concurrent
                )

        summary = await asyncio.gather(*(_run(c) for c in cols))
        logger.info("MIGRATION COMPLETE: %s", json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
