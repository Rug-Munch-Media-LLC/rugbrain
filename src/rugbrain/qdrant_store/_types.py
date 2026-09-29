"""Shared structural types for the QdrantVectorStore mixins.

The store is composed from mixins (search / hybrid / write) that call
methods and attributes defined on the other mixins or the core. This
Protocol declares that shared surface so mypy can type-check each mixin
in isolation; it is never instantiated at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class HybridRrfQuery:
    """Parameters for native Qdrant hybrid (dense+sparse RRF) search."""

    collection: str
    dense_vector: list[float]
    sparse: tuple[list[int], list[float]]
    limit: int
    qdrant_filter: dict[str, Any] | None = field(default=None)
    dense_name: str | None = field(default=None)
    sparse_name: str | None = field(default=None)


class StoreSurface(Protocol):
    """Surface shared across QdrantVectorStore mixins."""

    async def _get_client(self) -> httpx.AsyncClient: ...

    async def _collection_supports_sparse(
        self, collection: str, client: httpx.AsyncClient
    ) -> bool: ...

    async def _collection_uses_named_vectors(
        self, collection: str, client: httpx.AsyncClient
    ) -> bool: ...

    async def _get_dense_vector_name(
        self, collection: str, client: httpx.AsyncClient, expected_dim: int | None = None
    ) -> str | None: ...

    async def _get_sparse_vector_name(
        self, collection: str, client: httpx.AsyncClient
    ) -> str | None: ...

    async def search(
        self,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.6,
        filters: dict[str, Any] | None = None,
        allow_create: bool = False,
    ) -> list[dict[str, Any]]: ...

    async def _hybrid_search_rrf(self, req: HybridRrfQuery) -> list[dict[str, Any]] | None: ...
