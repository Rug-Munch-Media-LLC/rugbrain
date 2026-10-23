"""Qdrant storage for the X social-intel lane.

Isolated from ``pipeline`` so the cycle orchestrator stays under the 200-line
cap. Holds the ``CrawlDocument`` shape adapter and the embed + upsert call —
the only part of the cycle that talks to Qdrant.

``store_docs`` returns COUNTS, not a single number. ``store_documents`` already
distinguishes inserted / failed / skipped chunks; collapsing that to ``inserted``
is how a dead embedding lane reported a green, empty cycle: every chunk failed,
``inserted`` was 0, and the cycle log said exactly that with no cause attached.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("rugbrain.x_intel")

#: Every doc this lane writes is social intel, whatever the tweet said.
DOC_CATEGORY: str = "x_social"


class _TextEmbedder:
    """Adapter giving ``store_documents`` the single-embedding interface."""

    async def embed_one(self, text: str) -> list[float]:
        """Embed one document text with the shared embedder."""
        from rugbrain.embeddings import get_embedding

        return await get_embedding(text)


def build_crawl_docs(docs: list[dict[str, Any]]) -> list[Any]:
    """Adapt normalized tweet docs into ``ingest_common.CrawlDocument`` objects."""
    from rugbrain.ingest_common import CrawlDocument

    return [
        CrawlDocument(
            title=d["content"][:120],
            content=d["content"],
            url=d["metadata"].get("url", ""),
            source=d["source"],
            collection=d["collection"],
            category=DOC_CATEGORY,
            published=d["metadata"].get("ts", ""),
            severity=d["severity"],
            metadata=d["metadata"],
        )
        for d in docs
    ]


def store_reason(res: dict[str, int]) -> str:
    """Why a store batch landed what it landed ("" when it simply worked)."""
    if res.get("inserted"):
        return ""
    if res.get("failed"):
        return "embed_or_upsert_failed"
    if res.get("skipped"):
        return "already_present"
    return "no_chunks"


async def store_docs(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Embed + upsert *docs* into Qdrant. Returns counts plus a failure reason.

    Fail-open: a dead embedder or store yields ``inserted=0`` with
    ``reason='embed_or_upsert_failed'`` so the cycle can report the cause
    instead of a bare zero.
    """
    from rugbrain.ingest_common import store_documents
    from rugbrain.qdrant_store import get_qdrant_store

    try:
        res = await store_documents(
            build_crawl_docs(docs), _TextEmbedder(), await get_qdrant_store()
        )
    except Exception as exc:
        logger.warning("xintel:store_failed docs=%d err=%s", len(docs), str(exc)[:160])
        return {
            "inserted": 0,
            "failed": len(docs),
            "skipped": 0,
            "chunks": 0,
            "reason": f"store_failed:{str(exc)[:80]}",
        }
    counts = {k: int(res.get(k, 0)) for k in ("inserted", "failed", "skipped", "chunks")}
    counts["reason"] = store_reason(counts)
    return counts


__all__ = ["DOC_CATEGORY", "build_crawl_docs", "store_docs", "store_reason"]
