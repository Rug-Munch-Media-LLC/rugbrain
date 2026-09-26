"""Qdrant storage for the X social-intel lane.

Isolated from ``pipeline`` so the cycle orchestrator stays under the 200-line
cap. Holds the ``CrawlDocument`` shape adapter and the embed + upsert call —
the only part of the cycle that talks to Qdrant.
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


async def store_docs(docs: list[dict[str, Any]]) -> int:
    """Embed + upsert *docs* into Qdrant. Returns the inserted count (fail-open)."""
    from rugbrain.ingest_common import store_documents
    from rugbrain.qdrant_store import get_qdrant_store

    res = await store_documents(build_crawl_docs(docs), _TextEmbedder(), await get_qdrant_store())
    return int(res.get("inserted", 0))


__all__ = ["DOC_CATEGORY", "build_crawl_docs", "store_docs"]
