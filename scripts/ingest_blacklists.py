#!/usr/bin/env python3
"""Ingest consolidated Web3 phishing/blacklist threat intel into ``known_scams``.

Source: the ``blacklists`` ingest source (``rugbrain.ingest_sources.blacklists``)
which aggregates free, no-auth GitHub raw feeds into labeled address/domain
entities:

  * ScamSniffer scam-database       — daily Web3 phishing domains + addresses
  * MetaMask eth-phishing-detect    — community Web3 domain blocklist
  * MyEtherWallet ethereum-lists    — darklisted addresses + malicious URLs
  * PhishFort phishfort-lists       — counter-phishing domain lists
  * Phishing.Database               — generic phishing domains (capped)

Each entity is embedded (1024-dim bge-m3) and upserted into ``known_scams``
with canonical metadata (entity_type, entity, labels, source, source_url),
so the bot's ``scam_patterns`` / entity data reflects real-time blacklists.

Usage:
    python scripts/ingest_blacklists.py --dry-run --limit 20
    python scripts/ingest_blacklists.py --limit 5000
    python scripts/ingest_blacklists.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3

Env knobs (no hardcoded credentials):
  - BLACKLIST_SOURCES       comma-separated feed keys to enable (default all)
  - BLACKLIST_MAX_PER_SOURCE  per-feed entity cap (default 10000)
  - PROXY_URL               optional residential proxy for fetches
  - GITHUB_TOKEN            optional GitHub API token (raw fetches don't need it)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.blacklists import BlacklistsSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_blacklists")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import build_doc, write_docs  # noqa: E402

COLLECTION = "known_scams"
SOURCE = "blacklists"


def _to_docs(docs: list[IngestDoc]) -> list[dict[str, Any]]:
    """Bridge source IngestDocs into the canonical script doc shape."""
    out: list[dict[str, Any]] = []
    for doc in docs:
        metadata = dict(doc.metadata)
        severity = (
            "critical"
            if metadata.get("entity_type") == "address"
            else metadata.get("severity", "high")
        )
        rel_key = (
            doc.doc_id.removeprefix(f"{SOURCE}:")
            if doc.doc_id.startswith(f"{SOURCE}:")
            else doc.doc_id
        )
        out.append(
            build_doc(
                source=SOURCE,
                rel_key=rel_key,
                content=doc.content,
                metadata=metadata,
                severity=severity,
                chain=str(metadata.get("ecosystem", "multi")),
                collection=COLLECTION,
            )
        )
    return out


def _print_sample(docs: list[IngestDoc], n: int) -> None:
    """Print up to *n* normalized entities (dry-run proof the parser works)."""
    print(f"\nSample of {min(n, len(docs))} normalized entities:")
    for doc in docs[:n]:
        meta = doc.metadata
        print(
            f"  [{meta.get('feed')}] {meta.get('entity_type'):>7} "
            f"labels={','.join(meta.get('labels') or []):<16} {meta.get('entity')}"
        )


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    src = BlacklistsSource()
    docs = await src.fetch(limit=args.limit)
    logger.info("blacklists_fetched entities=%d", len(docs))
    if not docs:
        logger.warning("no blacklist entities fetched (network/rate limit?)")
        return {"inserted": 0, "chunks": 0, "failed": 0}

    if args.dry_run:
        _print_sample(docs, args.sample)
        by_feed: dict[str, int] = {}
        for doc in docs:
            by_feed[doc.metadata.get("feed", "?")] = (
                by_feed.get(doc.metadata.get("feed", "?"), 0) + 1
            )
        print("\nPer-feed entity counts:")
        for feed, count in sorted(by_feed.items()):
            print(f"  {feed:<22} {count}")

    docs_out = _to_docs(docs)
    store = None
    try:
        from rugbrain.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        async with httpx.AsyncClient(timeout=120.0) as client:
            stats = await write_docs(
                client,
                store,
                docs_out,
                source=SOURCE,
                collection=COLLECTION,
                dry_run=args.dry_run,
            )
    finally:
        if store is not None:
            await store.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest consolidated Web3 phishing blacklists into known_scams"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview counts + entity sample only"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max entities to fetch (all feeds)")
    parser.add_argument("--sample", type=int, default=15, help="Entities to print in dry-run")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"BLACKLISTS INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
