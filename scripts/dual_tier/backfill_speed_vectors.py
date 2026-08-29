"""Backfill speed_vector (Qwen3-Embedding-0.6B, 1024-dim) across the corpus.

Iterates the crypto_intelligence collection, embeds each document through the
speed embedder (:8007), and writes ONLY the speed_vector named vector —
qwen_vector payloads are untouched.

SAFETY:
  * Refuses to run while rag-embed-migration.service is active unless --force
    (the migration owns the deep embedder; competing starves both).
  * Rate-limited: --batch-size / --sleep-secs keep the speed embedder at a
    fraction of its capacity so consumer chat latency is protected.
  * Resumable: points get payload marker speed_embedded=true after their
    vector lands; re-runs skip marked points via an IsEmpty filter.

Usage:
  python -m scripts.dual_tier.backfill_speed_vectors            # guarded run
  python -m scripts.dual_tier.backfill_speed_vectors --force \
      --batch-size 32 --sleep-secs 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time

import aiohttp
from qdrant_client import AsyncQdrantClient, models

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("RAG_COLLECTION", "crypto_intelligence")
SPEED_EMBED = os.getenv("SPEED_EMBED_URL", "http://127.0.0.1:8007")
MARKER_FIELD = "speed_embedded"


def migration_active() -> bool:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "rag-embed-migration"],  # noqa: S607 — fixed path, no user input
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        return out == "active"
    except Exception:
        return False


async def backfill(batch_size: int, sleep_secs: float) -> None:
    client = AsyncQdrantClient(url=QDRANT_URL)
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120))
    t0 = time.time()
    done = skipped = errors = 0
    offset = None

    try:
        while True:
            points, offset = await client.scroll(
                collection_name=COLLECTION,
                limit=batch_size,
                offset=offset,
                with_payload=[MARKER_FIELD, "text"],
                with_vectors=False,
            )
            if not points:
                break

            todo = [p for p in points if not ((p.payload or {}) if p.payload else {}).get(MARKER_FIELD)]
            skipped += len(points) - len(todo)
            if todo:
                texts = [(p.payload or {}).get("text", "") for p in todo]
                ids = [str(p.id) for p in todo]
                async with session.post(
                    f"{SPEED_EMBED}/v1/embeddings", json={"input": texts}
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                embeddings = [None] * len(texts)
                for emb in data["data"]:
                    embeddings[emb["index"]] = emb["embedding"]

                await client.update_vectors(
                    collection_name=COLLECTION,
                    vector_name="speed_vector",
                    points=models.Batch(ids=ids, vectors=embeddings),  # type: ignore[arg-type]  # qdrant-client Batch stubs stricter than runtime
                )
                # Mark AFTER the vector landed (crash-safe resume).
                await client.set_payload(
                    collection_name=COLLECTION,
                    payload={MARKER_FIELD: True},
                    points=models.PointIdsList(points=ids),  # type: ignore[arg-type]
                )
                done += len(todo)

            print(f"  progress: done={done} skipped={skipped} errors={errors}", flush=True)
            if sleep_secs:
                await asyncio.sleep(sleep_secs)
            if offset is None:
                break
    finally:
        await session.close()
        await client.close()
        print(
            f"backfill complete: done={done} skipped={skipped} errors={errors} "
            f"in {time.time() - t0:.0f}s"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--sleep-secs", type=float, default=0.5)
    ap.add_argument(
        "--force", action="store_true", help="run even while rag-embed-migration is active"
    )
    args = ap.parse_args()

    if migration_active() and not args.force:
        print("REFUSING: rag-embed-migration is active — it owns the deep embedder.")
        print("Re-run after it completes, or pass --force to override.")
        sys.exit(2)

    asyncio.run(backfill(args.batch_size, args.sleep_secs))


if __name__ == "__main__":
    main()
