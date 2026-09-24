"""QdrantVectorStore core: client lifecycle and per-collection probes."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class QdrantVectorStoreCore:
    """Client/connection half of ``QdrantVectorStore``.

    Holds the base URL, lazily-created ``httpx.AsyncClient`` and the
    per-instance collection-capability caches (sparse support, named
    vectors). Search/write behaviour lives in the mixins combined in
    ``rugbrain.qdrant_store.store``.
    """

    def __init__(self, url: str | None = None):
        import rugbrain.qdrant_store as _pkg

        self._url = (url or _pkg.QDRANT_URL).rstrip("/")
        # An explicitly provided URL wins over the env-based fallback
        # resolution (which probes QDRANT_FALLBACK_URLS). Without this, the
        # constructor argument was silently ignored by _get_client().
        self._explicit_url = url is not None
        self._client: httpx.AsyncClient | None = None
        # Per-instance cache of collection params and vector capabilities.
        # Qdrant schemas are immutable, so one probe per collection is enough.
        self._params_cache: dict[str, dict[str, Any] | None] = {}
        self._sparse_name_cache: dict[str, str | None] = {}
        self._dense_name_cache: dict[str, str | None] = {}

    @property
    def url(self) -> str:
        return self._url

    async def _get_client(self) -> httpx.AsyncClient:
        import rugbrain.qdrant_store as _pkg

        if self._client is None:
            resolved = self._url if self._explicit_url else await _pkg._resolve_qdrant_url()
            logger.debug("qdrant_client_init url=%s", resolved)
            self._client = httpx.AsyncClient(base_url=resolved, timeout=30.0)
        return self._client

    async def _get_collection_params(
        self, collection: str, client: httpx.AsyncClient
    ) -> dict[str, Any] | None:
        """Fetch and cache a collection's Qdrant parameter object."""
        if collection in self._params_cache:
            return self._params_cache[collection]
        params: dict[str, Any] | None = None
        try:
            response = await client.get(f"/collections/{collection}")
            if response.status_code == 200:
                result = response.json().get("result", {}) or {}
                candidate = result.get("config", {}).get("params", {}) or {}
                if isinstance(candidate, dict):
                    params = candidate
        except Exception as exc:
            logger.debug("qdrant_collection_params_failed collection=%s err=%s", collection, exc)
        self._params_cache[collection] = params
        return params

    async def _get_dense_vector_name(
        self,
        collection: str,
        client: httpx.AsyncClient,
        expected_dim: int | None = None,
    ) -> str | None:
        """Return the configured dense vector name, or ``None`` for bare vectors."""
        if collection in self._dense_name_cache:
            return self._dense_name_cache[collection]
        import rugbrain.qdrant_store as _pkg

        name: str | None = None
        params = await self._get_collection_params(collection, client)
        vectors = params.get("vectors") if params else None
        if isinstance(vectors, dict) and "size" not in vectors:
            candidates = {
                key: value
                for key, value in vectors.items()
                if isinstance(key, str) and key and isinstance(value, dict)
            }
            if _pkg.DENSE_VECTOR_NAME in candidates:
                name = _pkg.DENSE_VECTOR_NAME
            elif expected_dim is not None:
                matching = [
                    key for key, spec in candidates.items() if spec.get("size") == expected_dim
                ]
                if len(matching) == 1:
                    name = matching[0]
            elif len(candidates) == 1:
                name = next(iter(candidates))
            elif len(candidates) > 1:
                logger.error(
                    "qdrant_dense_vector_ambiguous collection=%s names=%s dim=%s",
                    collection,
                    sorted(candidates),
                    expected_dim,
                )
        self._dense_name_cache[collection] = name
        return name

    async def _get_sparse_vector_name(
        self, collection: str, client: httpx.AsyncClient
    ) -> str | None:
        """Return the configured sparse vector name, if any."""
        if collection in self._sparse_name_cache:
            return self._sparse_name_cache[collection]
        params = await self._get_collection_params(collection, client)
        sparse = params.get("sparse_vectors") if params else None
        name = next(iter(sparse), None) if isinstance(sparse, dict) else None
        if not isinstance(name, str) or not name:
            name = None
        self._sparse_name_cache[collection] = name
        return name

    async def _collection_supports_sparse(self, collection: str, client: httpx.AsyncClient) -> bool:
        """Return whether *collection* exposes a named sparse vector."""
        return await self._get_sparse_vector_name(collection, client) is not None

    async def _collection_uses_named_vectors(
        self, collection: str, client: httpx.AsyncClient
    ) -> bool:
        """Return whether Qdrant requires a named dense vector."""
        params = await self._get_collection_params(collection, client)
        vectors = params.get("vectors") if params else None
        return isinstance(vectors, dict) and "size" not in vectors
