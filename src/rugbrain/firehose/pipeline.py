"""Smart ingestion pipeline — dedup, quality gate, embed, store.

Filters, deduplicates, and enriches documents before RAG storage in the
local Qdrant store (no rmi-backend round-trip, docs/MODERNIZATION.md §5.2).
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from .config import MIN_QUALITY_SCORE, RATE_LIMITS
from .quality import extract_entities, make_content_hash, score_quality

logger = logging.getLogger("rag.firehose")

__all__ = ["IngestionPipeline"]


class IngestionPipeline:
    """Filters, deduplicates, and enriches documents before RAG storage."""

    def __init__(self) -> None:
        self._rate_trackers: dict[str, list[float]] = {}

    def _check_rate(self, collection: str) -> bool:
        """Return True if we're under the rate limit for this collection."""
        limit = RATE_LIMITS.get(collection, RATE_LIMITS["default"])
        now = time.monotonic()
        times = self._rate_trackers.setdefault(collection, [])
        times[:] = [t for t in times if now - t < 60]
        return len(times) < limit

    def _record_ingest(self, collection: str) -> None:
        """Record an ingestion for rate limiting."""
        self._rate_trackers.setdefault(collection, []).append(time.monotonic())

    async def is_duplicate(self, content_hash: str, collection: str) -> bool:
        """True when this content hash is already stored in local Qdrant.

        The deterministic point id IS the content hash, so ``has_point`` is
        the dedup oracle. Fail-open (returns False) so a store error never
        drops fresh content.
        """
        try:
            from rugbrain.qdrant_store import get_qdrant_store

            store = await get_qdrant_store()
            return await store.has_point(content_hash, collection)
        except Exception:
            logger.warning("dedup check failed — proceeding with ingest", exc_info=True)
        return False

    async def ingest(
        self, collection: str, content: str, metadata: dict[str, Any], doc_id: str | None = None
    ) -> dict[str, Any]:
        """Run the full pipeline: dedup → quality → extract → classify → store."""

        content_hash = make_content_hash(content)
        if await self.is_duplicate(content_hash, collection):
            return {"status": "duplicate", "hash": content_hash}

        quality = score_quality(content, metadata)
        if quality < MIN_QUALITY_SCORE:
            return {"status": "skipped", "reason": "low_quality", "score": quality}

        entities = extract_entities(content)
        metadata.update(
            {
                "entities": entities,
                "quality_score": quality,
                "content_hash": content_hash,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
        )

        # ── PII scrubbing: redact wallet addresses, emails, etc. ──
        from rugbrain.pii_scrubber import scrub_text

        content = await scrub_text(content)

        if not self._check_rate(collection):
            return {"status": "rate_limited", "collection": collection}

        try:
            # Embed and upsert DIRECTLY into the local Qdrant store. No
            # rmi-backend round-trip (docs/MODERNIZATION.md §5.2).
            from rugbrain.embedder import get_embedder
            from rugbrain.qdrant_store import get_qdrant_store

            embedder = get_embedder()
            store = await get_qdrant_store()
            embedding = await embedder.embed_one(content[:8000])
            if not embedding:
                return {"status": "error", "detail": "embedding backend returned no vector"}

            point_id = doc_id or content_hash
            ok = await store.insert(
                doc_id=point_id,
                collection=collection,
                embedding=embedding,
                content=content,
                metadata=metadata,
                source=str(metadata.get("source", "")),
                severity=str(metadata.get("severity", "medium")),
                chain=str(metadata.get("chain", "")),
            )
            self._record_ingest(collection)
            if not ok:
                return {"status": "error", "detail": "local qdrant insert failed"}
            return {
                "status": "ingested",
                "id": point_id,
                "quality": quality,
                "entities": entities,
            }
        except Exception as e:
            return {"status": "error", "detail": str(e)[:200]}

    async def ingest_batch(self, docs: list[dict[str, Any]]) -> dict[str, int]:
        """Ingest multiple documents with rate limiting."""
        stats = {"ingested": 0, "duplicate": 0, "skipped": 0, "errors": 0}

        for doc in docs:
            collection = doc.get("collection", "general")
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})
            doc_id = doc.get("doc_id")

            result = await self.ingest(collection, content, metadata, doc_id)
            status = result.get("status", "error")
            if status == "ingested":
                stats["ingested"] += 1
            elif status == "duplicate":
                stats["duplicate"] += 1
            elif status == "skipped":
                stats["skipped"] += 1
            else:
                stats["errors"] += 1

            await asyncio.sleep(0.05)

        return stats
