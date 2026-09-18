"""Shared structural types for the QdrantVectorStore mixins.

The store is composed from mixins (search / hybrid / write) that call
methods and attributes defined on the other mixins or the core. This
Protocol declares that shared surface so mypy can type-check each mixin
in isolation; it is never instantiated at runtime.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class StoreSurface(Protocol):
    """Surface shared across QdrantVectorStore mixins."""

    async def _get_client(self) -> httpx.AsyncClient: ...

    async def _collection_supports_sparse(
        self, collection: str, client: httpx.AsyncClient
    ) -> bool: ...

    async def _collection_uses_named_vectors(
        self, collection: str, client: httpx.AsyncClient
    ) -> bool: ...

    async def search(
        self,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.6,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def _hybrid_search_rrf(
        self,
        client: httpx.AsyncClient,
        collection: str,
        dense_vector: list[float],
        sparse: tuple[list[int], list[float]],
        limit: int,
        qdrant_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None: ...
