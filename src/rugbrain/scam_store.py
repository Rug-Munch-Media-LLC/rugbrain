"""Qdrant/Redis plumbing for scam patterns: read, cache, persist.

One job each: scroll the threat-intel collection into memory, serve the
aggregated list through a 24h Redis cache, and write patterns into the
``scam_patterns`` collection. The aggregation itself is pure and lives in
:mod:`rugbrain.scam_aggregate`.

Clients and embedders are imported lazily inside the coroutines so that an
unrelated import failure elsewhere in the package cannot make this module
unimportable from a pure test.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

THREAT_INTEL_COLLECTION = "threat_intel_v2"
SCAM_PATTERNS_COLLECTION = "scam_patterns"

# Redis cache key for the aggregated pattern list (JSON).
PATTERN_CACHE_KEY = "rag:scam_patterns:cache"
PATTERN_CACHE_TTL = 86400  # 24h

__all__ = [
    "PATTERN_CACHE_KEY",
    "PATTERN_CACHE_TTL",
    "SCAM_PATTERNS_COLLECTION",
    "THREAT_INTEL_COLLECTION",
    "extract_scam_patterns_from_intel",
    "get_scam_patterns",
    "store_scam_patterns",
]


def _pattern_content(pattern: dict[str, Any]) -> str:
    """The text embedded and stored for one scam pattern."""
    return (
        f"SCAM PATTERN: {pattern['attack_type']} ({pattern['technique']})\n"
        f"Protocols: {', '.join(pattern['affected_protocols'][:10])}\n"
        f"Chain: {pattern['chain']} | Severity: {pattern['severity']}\n"
        f"Funds lost: ${pattern['funds_lost']:,.0f}\n"
        f"Indicators: {', '.join(pattern['indicators'][:12])}\n"
        f"Reference incidents: {', '.join(pattern['reference_incidents'][:5])}"
    )


async def extract_scam_patterns_from_intel(
    limit: int = 1000,
    collection: str = THREAT_INTEL_COLLECTION,
) -> list[dict[str, Any]]:
    """Scroll the threat-intel collection and aggregate into scam patterns."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore()
    docs: list[dict[str, Any]] = []
    offset: str | None = None
    try:
        while len(docs) < limit:
            batch, offset = await store.scroll(
                collection, limit=min(500, limit - len(docs)), offset=offset
            )
            if not batch:
                break
            docs.extend(batch)
            if not offset:
                break
    finally:
        await store.close()

    logger.info("scam_patterns: read %d docs from %s", len(docs), collection)
    from rugbrain.scam_aggregate import extract_scam_patterns_from_docs

    return extract_scam_patterns_from_docs(docs)


async def get_scam_patterns(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return the aggregated scam-pattern list.

    Checks the Redis cache first (24h TTL); on miss, re-extracts from the
    threat-intel collection and refreshes the cache. ``force_refresh``
    bypasses the cache entirely. A cache that cannot be read or written is a
    miss, never a failure -- the patterns are still extracted.
    """
    from typing import cast

    from rugbrain.search_service import _get_redis

    if not force_refresh:
        try:
            r = await _get_redis()
            cached = await r.get(PATTERN_CACHE_KEY)
            if cached:
                return cast("list[dict[str, Any]]", json.loads(cached))
        except Exception as e:
            logger.debug("scam_patterns cache read failed: %s", e)

    patterns = await extract_scam_patterns_from_intel()

    try:
        r = await _get_redis()
        await r.setex(PATTERN_CACHE_KEY, PATTERN_CACHE_TTL, json.dumps(patterns))
    except Exception as e:
        logger.debug("scam_patterns cache write failed: %s", e)

    return patterns


async def store_scam_patterns(patterns: list[dict[str, Any]]) -> dict[str, Any]:
    """Store structured scam patterns into the ``scam_patterns`` collection.

    Uses the standard semantic embedding (1024-dim qwen3-embedding-8b via Qwen3)
    so the collection stays queryable by the same embedder as the rest of the
    RAG. Returns ``{"stored": N, "skipped": M, "patterns": P}``. A pattern whose
    embedding fails is skipped, not fatal: one bad pattern must not cost the
    whole ingest.
    """
    from rugbrain.embeddings import get_embedding
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore()
    stored = 0
    skipped = 0
    try:
        for pattern in patterns:
            content = _pattern_content(pattern)
            try:
                embedding = await get_embedding(content)
            except Exception as e:
                logger.warning("scam_patterns embed failed for %s: %s", pattern["pattern_id"], e)
                skipped += 1
                continue

            ok = await store.insert(
                doc_id=f"scam_pattern:{pattern['pattern_id']}",
                collection=SCAM_PATTERNS_COLLECTION,
                embedding=embedding,
                content=content,
                metadata={
                    "pattern_id": pattern["pattern_id"],
                    "attack_type": pattern["attack_type"],
                    "technique": pattern["technique"],
                    "severity": pattern["severity"],
                    "chain": pattern["chain"],
                    "funds_lost": pattern["funds_lost"],
                    "affected_protocols": pattern["affected_protocols"][:20],
                    "reference_incidents": pattern["reference_incidents"][:20],
                    "indicators": pattern["indicators"][:20],
                    "doc_count": pattern["doc_count"],
                },
                source="scam_pattern_extractor",
                severity=pattern["severity"],
                chain=pattern["chain"],
            )
            if ok:
                stored += 1
            else:
                skipped += 1
    finally:
        await store.close()

    logger.info("scam_patterns stored=%d skipped=%d", stored, skipped)
    return {"stored": stored, "skipped": skipped, "patterns": len(patterns)}
