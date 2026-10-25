#!/usr/bin/env python3
"""Ingest the GambleFi / crypto-gaming snapshot into ``gamblefi_docs`` (4096-d).

Input is the deduped ``pages.jsonl`` written by
``scripts/fetch_gamblefi_docs.py`` — official casino, provably-fair, on-chain
casino and GameFi docs/whitepapers/contracts. Nothing is fetched here: the
ingest is a pure function of the snapshot, so a re-run is reproducible.

Chunking is the shared section-aware rule in ``scripts/docs_chunker.py``: a
chunk keeps its heading path, fenced blocks (ABIs, curl examples) and markdown
tables stay atomic, and tokenomics tables are never split from their header.

Embedding goes through ``embed_lane.embed_batch``, which walks the corpus lane
chain (SiliconFlow .com 8B/4096 -> DeepInfra 8B/4096 -> local :8024) and
dimension-checks every response: a 1024/2560 lane cannot be stored here. The
dim is asserted again before the write, because the ingest is what the gate
protects.

Usage
    python scripts/ingest_gamblefi_docs.py --dry-run       # chunk + count only
    python scripts/ingest_gamblefi_docs.py --limit 20      # bounded batch
    python scripts/ingest_gamblefi_docs.py                 # full ingest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docs_chunker import chunk_pages

COLLECTION = "gamblefi_docs"
SOURCE = "gamblefi-docs"
EMBED_DIM = 4096
BATCH = 8
DEFAULT_SNAPSHOT = Path("data/gamblefi_docs/pages.jsonl")


def chunks(snapshot: Path) -> list[dict[str, Any]]:
    pages = [
        json.loads(line)
        for line in snapshot.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return chunk_pages(pages, SOURCE)


async def ingest(
    snapshot: Path, *, limit: int | None = None, dry_run: bool = False
) -> dict[str, int]:
    from embed_lane import embed_batch

    from rugbrain.qdrant_store import QdrantVectorStore

    units = await asyncio.to_thread(chunks, snapshot)
    if limit is not None:
        units = units[:limit]
    stats = {
        "pages": len({u["metadata"]["url"] for u in units}),
        "chunks": len(units),
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dim_mismatch": 0,
    }
    if dry_run or not units:
        return stats

    store = QdrantVectorStore()
    try:
        for start in range(0, len(units), BATCH):
            batch = units[start : start + BATCH]
            vectors = await embed_batch([c["text"] for c in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                if not vector:
                    stats["errors"] += 1
                    continue
                if len(vector) != EMBED_DIM:
                    stats["dim_mismatch"] += 1
                    continue
                stats["embedded"] += 1
                ok = await store.insert(
                    doc_id=chunk["doc_id"],
                    collection=COLLECTION,
                    embedding=vector,
                    content=chunk["text"],
                    metadata=chunk["metadata"],
                    source=chunk["metadata"]["url"],
                    severity="info",
                    chain="multi",
                )
                stats["upserted" if ok else "errors"] += 1
    finally:
        await store.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    ap.add_argument("--limit", type=int, default=None, help="max chunks to embed")
    ap.add_argument("--dry-run", action="store_true", help="chunk and count only")
    args = ap.parse_args()
    stats = asyncio.run(ingest(args.snapshot, limit=args.limit, dry_run=args.dry_run))
    print(json.dumps(stats, sort_keys=True))
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
