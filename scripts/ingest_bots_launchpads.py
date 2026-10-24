#!/usr/bin/env python3
"""Ingest public bot / launchpad / whitepaper docs into research_v2 (8B, 4096-d).

Documents come from ``config/bots_launchpads_manifest.json`` via the
``bots_launchpads`` source, so provenance (publisher, licence class, doc_type,
retrieved date, canonical id) travels with every chunk. The write target is the
declared collection ``research_v2``, which the lane resolver routes to
``research_v2_8b`` — the canonical 4096-dim lane.

The run REFUSES to write when the embedder is not 4096-dim: a silent 1024/2560
write into a 4096 collection is exactly the class of incident the corpus
manifest exists to prevent.

Idempotent: ``store_documents`` skips chunks whose deterministic point id is
already present, so re-runs cost nothing for unchanged pages.

Usage:
    python scripts/ingest_bots_launchpads.py --dry-run
    python scripts/ingest_bots_launchpads.py --limit 40 --category launchpad
    python scripts/ingest_bots_launchpads.py --report data/bots_launchpads_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.ingest_common import store_documents
from rugbrain.ingest_sources.base import IngestDoc, to_crawl_document
from rugbrain.ingest_sources.bot_launchpad_docs import BotLaunchpadDocsSource
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("ingest_bots_launchpads")

COLLECTION = "research_v2"
EXPECTED_DIM = 4096
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")


def _summary(docs: list[IngestDoc]) -> dict[str, int]:
    """Per-category and per-doc_type document counts."""
    counts: dict[str, int] = {}
    for d in docs:
        key = f"{d.metadata.get('category', '?')}/{d.doc_type}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _embedder() -> Qwen3Embedder:
    """The 8B embedder — refuse to run on any other width."""
    embedder = Qwen3Embedder()
    dims = int(getattr(embedder, "dims", 0) or 0)
    if dims != EXPECTED_DIM:
        raise SystemExit(
            f"refusing to write: embedder dim={dims} provider={getattr(embedder, 'provider', '?')}"
            f" (need {EXPECTED_DIM}; set DEEPINFRA_API_KEY or EMBED_PROVIDER=local on the 8B lane)"
        )
    log.info("embedder model=%s dim=%d", embedder.MODEL, dims)
    return embedder


async def run(dry_run: bool, limit: int | None, category: str | None) -> dict:
    """Fetch the manifest docs and embed them into ``research_v2``."""
    docs = await BotLaunchpadDocsSource(category=category).fetch(limit=limit)
    stats: dict = {
        "collection": COLLECTION,
        "routed_collection": f"{COLLECTION}_8b",
        "embed_dim": EXPECTED_DIM,
        "source": "bots_launchpads",
        "docs": len(docs),
        "by_category": _summary(docs),
        "with_parser_docling": sum(1 for d in docs if d.metadata.get("parser") == "docling"),
        "dry_run": dry_run,
        "inserted": 0,
        "chunks": 0,
        "skipped": 0,
        "failed": 0,
        "titles": [{"title": d.title, "url": d.url, "words": d.metadata.get("word_count", 0)} for d in docs],
    }
    if dry_run or not docs:
        return stats

    store = QdrantVectorStore(url=QDRANT_URL)
    result = await store_documents([to_crawl_document(d) for d in docs], _embedder(), store)
    stats.update({k: result[k] for k in ("inserted", "chunks", "skipped", "failed")})
    log.info(
        "bots_launchpads_done docs=%d inserted=%d chunks=%d skipped=%d failed=%d",
        stats["docs"], stats["inserted"], stats["chunks"], stats["skipped"], stats["failed"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest bot/launchpad/whitepaper docs")
    parser.add_argument("--dry-run", action="store_true", help="fetch + report, write nothing")
    parser.add_argument("--limit", type=int, default=None, help="max docs in this batch")
    parser.add_argument("--category", default=None, help="bot | launchpad | whitepaper")
    parser.add_argument("--report", default="", help="write the JSON report to this path")
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit, args.category))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k != "titles"}, indent=2))
    return 0 if stats["docs"] else 1


if __name__ == "__main__":
    sys.exit(main())
