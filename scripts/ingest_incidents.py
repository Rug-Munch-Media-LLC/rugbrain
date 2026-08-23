#!/usr/bin/env python3
"""Ingest DeFi incident / post-mortem / audit-findings sources into Qdrant.

Sources (all NEW — not covered by defihacklabs / rekt / defillama-hacks /
peckshield / web3hacks):

  * ``guttedefi``              — jwparktom/GutteDeFi: curated table of DeFi
    attacks (date, protocol, loss, post-mortem/analysis links, audited?).
  * ``immunefi_hack_analyses`` — immunefi-team/Web3-Security-Library
    HackAnalyses: root-cause post-mortems with vulnerability type.
  * ``blocksecteam_defi_poc``  — blocksecteam/defi_poc: per-protocol exploit
    PoCs (README + attack scripts).
  * ``slowmist_open_reports``  — slowmist/Knowledge-Base open-report:
    published SlowMist audit reports (markdown only).

Each source implements the programmatic ``Source`` interface
(``rugbrain.ingest_sources``), so the same source modules can also be driven
by ``python -m rugbrain.ingest_sources --source guttedefi``.

Collection routing follows docs/DATA-PLACEMENT.md + COLLECTION_REGISTRY.md:

  * guttedefi / immunefi_hack_analyses / blocksecteam_defi_poc (incidents,
    post-mortems, PoCs) → ``defi_exploits``
  * slowmist_open_reports (audit findings) → ``contract_audits``

The records are normalized into the canonical incident shape (title,
root-cause description, protocol, loss, date, vulnerability class, source
URL), chunked, embedded with qwen3-embedding-4b (2560-dim via ), and upserted
into their collection.

Idempotent: point IDs are deterministic (source:stable-key:content-hash →
UUIDv5 at the store), so re-running overwrites the same points instead of
duplicating them.

Usage:
    python scripts/ingest_incidents.py --dry-run
    python scripts/ingest_incidents.py --dry-run --limit 5
    python scripts/ingest_incidents.py --source guttedefi --limit 20
    python scripts/ingest_incidents.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
  - httpx; optional GITHUB_TOKEN to lift GitHub API rate limits
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from typing import Any

import httpx

from rugbrain.ingest_sources import fetch_source, list_sources
from rugbrain.ingest_sources.base import IngestDoc

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_incidents")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import write_docs  # noqa: E402

from rugbrain.qdrant_store import QdrantVectorStore  # noqa: E402

COLLECTION = "defi_exploits"

DEFAULT_SOURCES = (
    "guttedefi",
    "immunefi_hack_analyses",
    "blocksecteam_defi_poc",
    "slowmist_open_reports",
)


def _doc_to_ingest_dict(doc: IngestDoc) -> dict[str, Any]:
    """Bridge an IngestDoc into the ``write_docs`` dict shape.

    The target collection comes from the per-doc metadata (set by the
    source module, e.g. slowmist → ``contract_audits``), falling back to
    ``defi_exploits`` for incident sources.
    """
    metadata = dict(doc.metadata or {})
    return {
        "rel_key": doc.doc_id,
        "text": doc.content,
        "metadata": metadata,
        "severity": metadata.get("severity", "medium"),
        "chain": metadata.get("chain", "") or "",
        "collection": metadata.get("collection", COLLECTION),
    }


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    if args.list:
        print("available incident sources:")
        for name in DEFAULT_SOURCES:
            print(f"  - {name}")
        print(f"all registered sources: {', '.join(list_sources())}")
        return {"inserted": 0, "chunks": 0, "failed": 0}

    sources = tuple(args.source) if args.source else DEFAULT_SOURCES
    unknown = [s for s in sources if s not in list_sources()]
    if unknown:
        logger.error("unknown_sources=%s available=%s", unknown, list_sources())
        return {"inserted": 0, "chunks": 0, "failed": 0}

    all_docs: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    for name in sources:
        docs = await fetch_source(name, limit=args.limit)
        per_source[name] = len(docs)
        logger.info("source_fetched name=%s docs=%d", name, len(docs))
        all_docs.extend(docs)
        if args.dry_run and docs:
            print(f"\n── sample from {name} ({len(docs)} docs) ──")
            for d in docs[:3]:
                print(f"  • {d.title or d.doc_id}")
                print(f"    url: {d.url or '-'}")
                print(
                    f"    proto={d.metadata.get('protocol', '-')} "
                    f"date={d.metadata.get('date', '-')} "
                    f"loss={d.metadata.get('loss', d.metadata.get('loss_usd', '-'))} "
                    f"class={d.metadata.get('vulnerability_class', '-')}"
                )
    print(f"\ntotal fetched: {len(all_docs)} docs ({per_source})")

    if not all_docs:
        logger.error("no documents fetched; nothing to ingest")
        return {"inserted": 0, "chunks": 0, "failed": 0}

    docs = [_doc_to_ingest_dict(d) for d in all_docs]
    # Route each doc to its source-declared collection (incidents →
    # defi_exploits, slowmist audit findings → contract_audits). Group so
    # write_docs gets one collection per call (it writes the whole batch
    # to the collection argument).
    by_collection: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in docs:
        by_collection[str(d.get("collection") or COLLECTION)].append(d)

    store = QdrantVectorStore()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            stats = {"inserted": 0, "chunks": 0, "failed": 0}
            for coll, group in sorted(by_collection.items()):
                logger.info("collection_group coll=%s docs=%d", coll, len(group))
                part = await write_docs(
                    client,
                    store,
                    group,
                    source="incidents",
                    collection=coll,
                    dry_run=args.dry_run,
                )
                for key in stats:
                    stats[key] += part.get(key, 0)
    finally:
        await store.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest DeFi incident/post-mortem/audit findings into defi_exploits"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max docs per source")
    parser.add_argument(
        "--source", action="append", default=None, help="Restrict to a source (repeatable)"
    )
    parser.add_argument("--list", action="store_true", help="List available incident sources")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"INCIDENT INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
