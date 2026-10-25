#!/usr/bin/env python3
"""Store a bounded live Polymarket slice in the unified corpus (rag_documents).

Before this script the repo's only Polymarket artefact was a parquet/B2 job over
a static HuggingFace dataset: odds were never in the corpus, so a question like
"odds of a SOL ETF approval" had nothing to retrieve. This writes each active
market as one corpus document carrying its odds, resolution date, volume,
category, provenance URL and ``fetched_at`` — plus the freshness contract
(``stale`` after 6h) from ``rugbrain.prediction_markets_freshness``.

Embedding: the canonical 4096-dim 8B lane (``Qwen/Qwen3-Embedding-8B``), the
same lane ``rag_documents`` already uses — a 1024-dim write here would be a
dimension violation.

Usage:
    python scripts/ingest_prediction_markets.py --dry-run --limit 50
    python scripts/ingest_prediction_markets.py --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.ingest_common import store_documents
from rugbrain.ingest_sources.base import to_crawl_document
from rugbrain.ingest_sources.prediction_markets import PredictionMarkets
from rugbrain.prediction_markets_docs import COLLECTION as BASE_COLLECTION
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_prediction_markets")

# Unified corpus collection; the lane resolver owns the 8B twin, but the slice
# must be retrievable by the ordinary rag_documents path.
TARGET_COLLECTION = BASE_COLLECTION
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")


async def run(limit: int, dry_run: bool, refresh: bool = False) -> dict:
    stats: dict = {"collection": TARGET_COLLECTION, "fetched": 0, "inserted": 0,
                   "chunks": 0, "skipped": 0, "failed": 0, "dry_run": dry_run}
    docs = await PredictionMarkets().fetch(limit=limit)
    stats["fetched"] = len(docs)
    if not docs:
        logger.warning("prediction_markets_no_docs")
        return stats
    if dry_run:
        logger.info("dry_run docs=%d first=%s", len(docs), docs[0].doc_id)
        return stats

    crawl_docs = [to_crawl_document(d) for d in docs]
    for doc in crawl_docs:  # explicit: never let a doc land in an unmanaged collection
        doc.collection = TARGET_COLLECTION

    embedder = Qwen3Embedder(provider=os.getenv("EMBED_PROVIDER", "siliconflow"))
    store = QdrantVectorStore(url=QDRANT_URL)
    result = await store_documents(crawl_docs, embedder, store)
    stats.update(result)
    logger.info("prediction_markets_done %s", stats)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true",
                        help="re-ingest the slice (deterministic doc_id upserts the same rows)")
    args = parser.parse_args()
    stats = asyncio.run(run(limit=args.limit, dry_run=args.dry_run, refresh=args.refresh))
    print(f"Result: {stats}")
    return 0 if stats["fetched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
