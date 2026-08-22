#!/usr/bin/env python3
"""Ingest curated crypto-expert knowledge into the RugBrain RAG.

Part of the content-seeding expansion. Routes curated docs from
``crypto_expert_knowledge_data.py`` into the canonical RAG collections:

  - ``education``        — DeFi protocols/strategies, token analysis
                           fundamentals, market cycles, cross-chain
                           comparisons, wallet security best practices
  - ``coding_knowledge`` — code-adjacent guidance (cross-chain dev,
                           wallet dev security)
  - ``contract_security``— MEV/sandwich attack mechanics + smart-contract
                           defensive patterns
  - ``audit_findings``   — (reserved for audit case-study docs)

Each doc is chunked, embedded with the shared path (2560-dim
qwen3-embedding-4b) and upserted with canonical payload metadata (topic,
knowledge_type, collection). Point IDs are deterministic
(source + rel_key + content hash) so re-runs overwrite the same points
instead of duplicating them.

Usage:
    python scripts/ingest_crypto_expert_knowledge.py --dry-run
    python scripts/ingest_crypto_expert_knowledge.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at INFINITY_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
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

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_crypto_expert_knowledge")

sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from crypto_expert_knowledge_data import EXPERT_DOCS, collection_counts  # noqa: E402
from ingest_common import chunk_text, content_hash, embed_many  # noqa: E402

# Canonical target collections (mirror search engine routing).
EDUCATION_COLLECTION = "education"
CODING_COLLECTION = "coding_knowledge"
CONTRACT_SECURITY_COLLECTION = "contract_security"
AUDIT_COLLECTION = "audit_findings"

SOURCE = "crypto_expert_knowledge"

# Chunk sizes for long-form expert guides.
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 100


def _collection_for(doc: dict[str, Any]) -> str:
    """Resolve the target collection from the doc's routing hint."""
    _col: str = str(doc.get("collection", EDUCATION_COLLECTION))
    return _col


def _doc_points(
    doc: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any], str]]:
    """Build (doc_id, content, metadata, severity) chunks for one doc.

    Deterministic IDs: ``crypto-expert:<rel_key>:<chunk_hash>:<idx>`` so
    re-runs overwrite the same points (idempotent).
    """
    content = (doc.get("content") or "").strip()
    if not content:
        return []
    rel_key = doc.get("rel_key", "doc")
    title = doc.get("title", "")
    topic = doc.get("topic", "")
    knowledge_type = doc.get("knowledge_type", "expert_guide")
    collection = _collection_for(doc)
    windows = chunk_text(content, CHUNK_SIZE, CHUNK_OVERLAP)
    points: list[tuple[str, str, dict[str, Any], str]] = []
    for ci, window in enumerate(windows):
        doc_id = f"cexpert:{rel_key}:{content_hash(window)}:{ci}"
        metadata: dict[str, Any] = {
            "title": title,
            "topic": topic,
            "knowledge_type": knowledge_type,
            "collection": collection,
            "chunk_index": ci,
            "total_chunks": len(windows),
            "source_url": "",
        }
        severity = "info"
        points.append((doc_id, window, metadata, severity))
    return points


async def _ingest(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    docs: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, int]:
    """Chunk, embed, and upsert docs, grouped by target collection."""
    counts: dict[str, int] = defaultdict(int)

    # Build all points grouped by collection (batched per collection).
    by_collection: dict[str, list[tuple[str, str, dict[str, Any], str]]] = defaultdict(list)
    for doc in docs:
        for point in _doc_points(doc):
            by_collection[_collection_for(doc)].append(point)

    if dry_run:
        for collection, points in sorted(by_collection.items()):
            logger.info(
                "[DRY RUN] collection=%s docs=%d chunks=%d",
                collection,
                len([d for d in docs if _collection_for(d) == collection]),
                len(points),
            )
            counts[collection] = len(points)
        return dict(counts)

    for collection, points in sorted(by_collection.items()):
        for i in range(0, len(points), 8):
            batch = points[i : i + 8]
            vectors = await embed_many(client, [p[1] for p in batch])
            for (doc_id, content, metadata, severity), vector in zip(batch, vectors, strict=False):
                if not vector:
                    continue
                ok = await store.insert(
                    doc_id=doc_id,
                    collection=collection,
                    embedding=vector,
                    content=content,
                    metadata=metadata,
                    source=SOURCE,
                    severity=severity,
                    chain=str(metadata.get("collection", "")),
                )
                if ok:
                    counts[collection] += 1
        logger.info("collection=%s upserted=%d", collection, counts[collection])
    return dict(counts)


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    docs = EXPERT_DOCS
    if args.source:
        docs = [d for d in docs if args.source in d.get("rel_key", "")]

    logger.info("total_docs=%d routed=%s", len(docs), collection_counts())

    async with httpx.AsyncClient(timeout=120.0) as client:
        store = QdrantVectorStore()
        try:
            stats = await _ingest(client, store, docs, args.dry_run)
        finally:
            await store.close()
    return {"docs": len(docs), "collections": stats}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest curated crypto-expert knowledge into education / coding_knowledge / contract_security"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument(
        "--source", default=None, help="Only ingest docs whose rel_key contains this"
    )
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print("CRYPTO EXPERT KNOWLEDGE INGEST COMPLETE")
    for k, v in stats.items():
        if k == "collections":
            for col, n in v.items():
                print(f"  {col}: {n}")
        else:
            print(f"  {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
