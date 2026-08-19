"""Parent Document Retrieval for RAG Context Expansion.

Stores full documents in Redis keyed by parent_id so that when a chunk
matches a query, the surrounding parent document context can be retrieved
and attached to the result.

Standard 2026 RAG practice: store full documents alongside chunks, and
expand search results with parent context for richer generation.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

log = logging.getLogger(__name__)


class ParentRetriever:
    """Stores and retrieves parent documents in Redis for context expansion."""

    def __init__(self, redis: Any | None = None) -> None:
        """Args: redis: optional async Redis client for DI/testing."""
        self._redis = redis

    async def store_parent(self, doc_id: str, content: str, metadata: dict[str, Any]) -> None:
        """Store the full parent document in Redis.

        Args:
            doc_id: Unique document identifier (same as used for chunks).
            content: The full original document text.
            metadata: Document-level metadata dict.
        """
        try:
            from rugbrain.compat import get_redis_async, get_redis

            r = get_redis_async()
            if r is None:
                log.warning(
                    "parent_retriever: no Redis client, skipping store_parent for %s", doc_id
                )
                return
            key = f"rag:parent:{doc_id}"
            doc = {
                "doc_id": doc_id,
                "content": content,
                "metadata": metadata,
            }
            await r.set(key, json.dumps(doc))  # type: ignore[operator]
            log.debug("parent_retriever: stored parent doc %s", doc_id)
        except Exception as e:
            log.debug("parent_retriever: store_parent failed for %s: %s", doc_id, e)

    async def get_parent(self, doc_id: str) -> dict[str, Any] | None:
        """Retrieve a parent document from Redis.

        Args:
            doc_id: The document identifier.

        Returns:
            dict with keys doc_id, content, metadata, or None if not found.
        """
        try:
            from rugbrain.compat import get_redis_async, get_redis

            r = get_redis_async()
            if r is None:
                return None
            key = f"rag:parent:{doc_id}"
            data = await r.get(key)  # type: ignore[operator]
            if data:
                return cast(dict[str, Any], json.loads(data))
        except Exception as e:
            log.debug("parent_retriever: get_parent failed for %s: %s", doc_id, e)
        return None

    async def expand_context(
        self, chunks: list[dict[str, Any]], window_size: int = 1
    ) -> list[dict[str, Any]]:
        """Enrich a list of matched chunks with parent document context.

        For each chunk that has a doc_id, fetches the parent document and
        attaches parent_content and parent_metadata to the chunk dict.

        Args:
            chunks: List of matching chunk dicts, each should have doc_id or id.
            window_size: Unused; reserved for future windowed expansion.

        Returns:
            The same list with parent_content and parent_metadata added where available.
        """
        enriched = []
        for chunk in chunks:
            doc_id = chunk.get("doc_id") or chunk.get("id")
            if doc_id:
                parent = await self.get_parent(doc_id)
                if parent:
                    chunk["parent_content"] = parent.get("content", "")
                    chunk["parent_metadata"] = parent.get("metadata", {})
            enriched.append(chunk)
        return enriched

    async def expand_context_around_chunk(
        self, chunk: dict[str, Any], context_chars: int = 2000
    ) -> str:
        """For a single chunk, return surrounding text from the parent document.

        Finds where the chunk content appears in the parent document and
        returns up to context_chars of text before and after that position.

        Args:
            chunk: A single chunk dict with content and doc_id/id keys.
            context_chars: Number of characters of context before and after.

        Returns:
            A string slice of the parent document centered on the chunk.
        """
        doc_id = chunk.get("doc_id") or chunk.get("id")
        chunk_text: str = chunk.get("content", "")
        if not doc_id or not chunk_text:
            return chunk_text

        parent = await self.get_parent(doc_id)
        if not parent:
            return chunk_text

        parent_content: str = parent.get("content", "")
        if not parent_content:
            return chunk_text

        pos = parent_content.find(chunk_text)
        if pos == -1:
            return chunk_text

        start = max(0, pos - context_chars)
        end = min(len(parent_content), pos + len(chunk_text) + context_chars)

        snippet = parent_content[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(parent_content):
            snippet = snippet + "..."

        return snippet


async def get_parent_retriever() -> ParentRetriever:
    """Factory: returns a singleton ParentRetriever."""
    global _parent_retriever
    if _parent_retriever is None:
        _parent_retriever = ParentRetriever()
    return _parent_retriever


_parent_retriever: ParentRetriever | None = None
