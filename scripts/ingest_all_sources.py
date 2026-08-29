#!/usr/bin/env python3
"""Generic runner: fetch ALL registered rugbrain.ingest_sources and store to Qdrant.

The 48-source registry (rugbrain.ingest_sources.SOURCES — CertiK alerts,
SlowMist archive, CISA KEV, OpenPhish, URLHaus, OpenSanctions, blacklists,
OSSF malicious packages, DeFiHackLabs, Rekt, DeFiLlama, MEV, bridges, EIPs,
…) previously had no storage pipeline: `python -m rugbrain.ingest_sources`
only fetched and printed. This script closes that gap.

Flow per source:
  1. src.fetch() → list[IngestDoc] (fail-open per source)
  2. route to src.default_collection (fallback: metadata["collection"] or rag_docs)
  3. skip docs already embedded (Qdrant has_point on deterministic doc_id)
  4. chunk (ingest_common.chunk_text) → embed (DeepInfra qwen3-4b first,
     local llama.cpp fallback) → QdrantVectorStore.insert (idempotent UUIDv5)

Usage:
    python scripts/ingest_all_sources.py --dry-run
    python scripts/ingest_all_sources.py --source certik_alerts --limit 20
    python scripts/ingest_all_sources.py            # full run (cron: 6h)

Env: QDRANT_URL, DEEPINFRA_API_KEY / DEEPINFRA_KEY_FILE, QWEN_EMBED_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from rugbrain.ingest_sources import SOURCES, fetch_source, list_sources
from rugbrain.qdrant_store import QdrantVectorStore

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
)
from ingest_common import chunk_text, embed_many  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_all_sources")

DEFAULT_COLLECTION = "rag_docs"
EMBED_DIM = 2560


def _route_collection(source_name: str, default_collection: str, metadata: dict[str, Any]) -> str:
    return str(metadata.get("collection") or default_collection or DEFAULT_COLLECTION)


async def _store_docs(
    store: QdrantVectorStore,
    client: httpx.AsyncClient,
    source_name: str,
    default_collection: str,
    docs: list[Any],
    dry_run: bool,
) -> dict[str, int]:
    stats = {"fetched": len(docs), "embedded": 0, "skipped": 0, "errors": 0}
    by_collection: dict[str, list[tuple[str, str, dict[str, Any], str]]] = defaultdict(list)
    for d in docs:
        if not d.content or not d.content.strip():
            stats["skipped"] += 1
            continue
        coll = _route_collection(source_name, default_collection, d.metadata)
        by_collection[coll].append((d.doc_id, d.content, dict(d.metadata), d.source or source_name))

    for coll, group in sorted(by_collection.items()):
        pending: list[tuple[str, str, dict[str, Any], str]] = []
        for doc_id, content, metadata, source in group:
            try:
                if not dry_run and await store.has_point(doc_id, coll):
                    stats["skipped"] += 1
                    continue
            except Exception as e:
                logger.warning("has_point_fail doc=%s err=%s", doc_id[:60], str(e)[:100])
            pending.append((doc_id, content, metadata, source))
        if not pending:
            continue
        if dry_run:
            stats["embedded"] += len(pending)
            continue
        chunks: list[tuple[str, str, dict[str, Any], str, str]] = []
        for doc_id, content, metadata, source in pending:
            for i, ch in enumerate(chunk_text(content)):
                chunks.append((f"{doc_id}:c{i}" if i else doc_id, ch, metadata, source, doc_id))
        try:
            vectors = await embed_many(client, [c[1] for c in chunks])
        except Exception as e:
            logger.warning("embed_fail source=%s err=%s", source_name, str(e)[:150])
            stats["errors"] += len(chunks)
            continue
        for (point_id, ch, metadata, source, root_id), vec in zip(chunks, vectors, strict=False):
            if not vec:
                stats["errors"] += 1
                continue
            ok = await store.insert(
                doc_id=point_id,
                collection=coll,
                embedding=vec,
                content=ch,
                metadata={**metadata, "root_doc_id": root_id},
                source=source,
            )
            if ok:
                stats["embedded"] += 1
            else:
                stats["errors"] += 1
    return stats


async def run(source_filter: str | None, limit: int | None, dry_run: bool) -> dict[str, Any]:
    store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
    totals: dict[str, Any] = {
        "sources": 0,
        "fetched": 0,
        "embedded": 0,
        "skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    per_source: dict[str, dict[str, int]] = {}
    names = [source_filter] if source_filter else list_sources()
    async with httpx.AsyncClient(timeout=60.0) as client:
        for name in names:
            src = next((s for s in SOURCES if s.name == name), None)
            if src is None:
                logger.warning("unknown_source name=%s", name)
                continue
            try:
                docs = await fetch_source(name, limit=limit)
            except Exception as e:
                logger.warning("source_fetch_fail name=%s err=%s", name, str(e)[:150])
                docs = []
            stats = await _store_docs(store, client, name, src.default_collection, docs, dry_run)
            per_source[name] = stats
            for k in ("fetched", "embedded", "skipped", "errors"):
                totals[k] += stats[k]
            totals["sources"] += 1
            logger.info(
                "source_stored name=%s coll=%s fetched=%d embedded=%d skipped=%d errors=%d",
                name,
                src.default_collection or DEFAULT_COLLECTION,
                stats["fetched"],
                stats["embedded"],
                stats["skipped"],
                stats["errors"],
            )
    await store.close()
    totals["per_source"] = per_source
    return totals


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Fetch + store ALL rugbrain ingest_sources → Qdrant"
    )
    parser.add_argument("--source", default=None, help="single source name (default: all)")
    parser.add_argument("--limit", type=int, default=None, help="max docs per source")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    totals = asyncio.run(run(args.source, args.limit, args.dry_run))
    per_source = totals.pop("per_source", {})
    print(json.dumps(totals, indent=2, default=str))
    for name, st in sorted(per_source.items()):
        print(
            f"  {name:<28} fetched={st['fetched']:<5} embedded={st['embedded']:<5} skipped={st['skipped']:<5} errors={st['errors']}"
        )


if __name__ == "__main__":
    main()
