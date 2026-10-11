#!/usr/bin/env python3
"""Ingest PUBLIC contract audit write-ups into the RAG contract_audits collection.

Uses the CANONICAL 8B EMBEDDING LANE: ``Qwen/Qwen3-Embedding-8B`` via DeepInfra
(4096-dim), writing to ``contract_audits`` which the lane resolver routes to
``contract_audits_8b`` (4096-dim) on Qdrant at ``http://127.0.0.1:6334``.

Sources (all public, legally ingestible GitHub repos):
  1. OpenZeppelin audit reports — OpenZeppelin/openzeppelin-contracts/audits/
     (markdown audit reports + PDF reports with pypdf text extraction)
  2. Spearbit security content — spearbit/portfolio/content/
     (audit checklists, bridge security guides, reviews)
  3. Trail of Bits publications (delegates to the existing source)

Idempotent: point IDs are deterministic UUIDv5 derived from
``source + url + chunk hash`` (via ``store_documents`` + ``has_point``),
so re-runs overwrite the same points instead of duplicating them.

Usage:
    python scripts/ingest_contract_audits.py --dry-run
    python scripts/ingest_contract_audits.py --limit 10
    python scripts/ingest_contract_audits.py --source openzeppelin_audits
    python scripts/ingest_contract_audits.py --list

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - DeepInfra API key: DEEPINFRA_API_KEY (or EMBED_PROVIDER=local for llama.cpp)
  - Optional: GITHUB_TOKEN to lift the 60 req/h unauthenticated API limit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

# Ensure scripts/ can import both scripts/ helpers and rugbrain package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.ingest_common import store_documents
from rugbrain.ingest_sources.base import IngestDoc, to_crawl_document
from rugbrain.ingest_sources.openzeppelin_audits import OpenZeppelinAuditsSource
from rugbrain.ingest_sources.spearbit_content import SpearbitContentSource
from rugbrain.ingest_sources.trailofbits_publications import TrailOfBitsPublicationsSource
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_contract_audits")

COLLECTION = "contract_audits"  # lane resolver routes to contract_audits_8b
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")

AVAILABLE_SOURCES: dict[str, type] = {
    "openzeppelin_audits": OpenZeppelinAuditsSource,
    "spearbit_content": SpearbitContentSource,
    "trailofbits_publications": TrailOfBitsPublicationsSource,
}


async def _fetch_from_source(source: object, limit: int | None = None) -> list[IngestDoc]:
    """Fetch docs from a Source instance, fail-open."""
    try:
        docs = await source.fetch(limit=limit)  # type: ignore[union-attr]
    except Exception as e:
        logger.warning(
            "source_fetch_failed name=%s err=%s", getattr(source, "name", "?"), str(e)[:200]
        )
        return []
    logger.info("source_fetched name=%s docs=%d", getattr(source, "name", "?"), len(docs))
    return docs


def _to_crawl_docs(docs: list[IngestDoc]) -> list:
    """Bridge IngestDoc list → CrawlDocument list via to_crawl_document."""
    return [to_crawl_document(d) for d in docs]


async def run(
    dry_run: bool,
    limit: int | None = None,
    sources: list[str] | None = None,
) -> dict:
    """Fetch audit sources, embed with 8B lane, upsert to contract_audits_8b."""
    stats: dict = {
        "collection": COLLECTION,
        "routed_collection": "contract_audits_8b",
        "embed_model": "Qwen/Qwen3-Embedding-8B",
        "embed_dim": 4096,
        "total_docs": 0,
        "inserted": 0,
        "chunks": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": dry_run,
    }

    source_names = sources if sources else list(AVAILABLE_SOURCES.keys())
    all_docs: list[IngestDoc] = []
    for name in source_names:
        if name not in AVAILABLE_SOURCES:
            logger.warning("unknown_source name=%s available=%s", name, list(AVAILABLE_SOURCES))
            continue
        src = AVAILABLE_SOURCES[name]()
        docs = await _fetch_from_source(src, limit=limit)
        all_docs.extend(docs)
        stats[f"{name}_docs"] = len(docs)

    stats["total_docs"] = len(all_docs)
    if not all_docs:
        logger.info("contract_audits_no_docs_to_ingest")
        return stats

    if dry_run:
        logger.info("dry_run fetched=%d would_embed_and_upsert into=%s", len(all_docs), COLLECTION)
        return stats

    embedder = Qwen3Embedder(provider="deepinfra")
    logger.info("embedder_init provider=deepinfra model=%s dim=%d", embedder.MODEL, embedder.dims)
    store = QdrantVectorStore(url=QDRANT_URL)

    crawl_docs = _to_crawl_docs(all_docs)
    result = await store_documents(crawl_docs, embedder, store)
    stats["inserted"] = result["inserted"]
    stats["chunks"] = result["chunks"]
    stats["skipped"] = result["skipped"]
    stats["failed"] = result["failed"]

    logger.info(
        "contract_audits_done inserted=%d chunks=%d skipped=%d failed=%d collection=%s",
        stats["inserted"],
        stats["chunks"],
        stats["skipped"],
        stats["failed"],
        COLLECTION,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Public contract audits → Qdrant (8B/4096-dim, contract_audits_8b)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch only; preview counts")
    parser.add_argument("--limit", type=int, default=None, help="Max docs per source")
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Restrict to a source (repeatable). Available: " + ", ".join(AVAILABLE_SOURCES),
    )
    parser.add_argument("--list", action="store_true", help="List available audit sources")
    args = parser.parse_args()

    if args.list:
        print("available contract audit sources:")
        for name, cls in AVAILABLE_SOURCES.items():
            print(f"  - {name}: {cls.description}")
        return

    stats = asyncio.run(run(args.dry_run, args.limit, args.source))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
