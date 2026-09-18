"""QdrantVectorStore core: client lifecycle and per-collection probes."""

from __future__ import annotations

import logging

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
        # Per-instance cache of whether a collection carries a sparse
        # vector config (avoids a GET per insert/search in production).
        self._sparse_cache: dict[str, bool] = {}
        # Per-instance cache of whether a collection is configured with
        # named vectors (named dense and/or sparse). Named-vector
        # collections reject the bare vector format.
        self._named_cache: dict[str, bool] = {}

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

    async def _collection_supports_sparse(self, collection: str, client: httpx.AsyncClient) -> bool:
        """Return True when *collection* carries a sparse-vector config.

        Results are cached per store instance so callers pay one extra
        GET per collection per process.
        """
        cached = self._sparse_cache.get(collection)
        if cached is not None:
            return cached
        supports = False
        try:
            r = await client.get(f"/collections/{collection}")
            if r.status_code == 200:
                params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
                supports = bool(params.get("sparse_vectors"))
        except Exception as e:
            logger.debug("qdrant_sparse_probe_failed collection=%s err=%s", collection, e)
        self._sparse_cache[collection] = supports
        return supports

    async def _collection_uses_named_vectors(
        self, collection: str, client: httpx.AsyncClient
    ) -> bool:
        """Return True when *collection* is configured with named vectors.

        A collection created with a named-vector config (``{"dense": ...}``
        or ``{"dense": ..., "bm25": ...}``) rejects a bare vector with
        "Not existing vector name", so the caller must send the named
        dense format. Only legacy collections created with a bare
        ``{"size": N, "distance": "Cosine"}`` config accept the bare
        vector. Named dense-only collections (e.g. ``news_v2``) do NOT
        report sparse support, so callers must probe named vectors, not
        just ``sparse_vectors``, before choosing the request shape.

        Results are cached per store instance so callers pay one extra
        GET per collection per process.
        """
        import rugbrain.qdrant_store as _pkg

        cached = self._named_cache.get(collection)
        if cached is not None:
            return cached
        named = False
        try:
            r = await client.get(f"/collections/{collection}")
            if r.status_code == 200:
                params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
                vectors = params.get("vectors")
                # Named dense vector config is a dict mapping name → spec.
                # A legacy config is a plain ``{"size": N, "distance": ...}``
                # which also happens to be a dict — distinguish by checking
                # for the named dense key we actually write.
                if isinstance(vectors, dict) and _pkg.DENSE_VECTOR_NAME in vectors:
                    named = True
                if params.get("sparse_vectors"):
                    named = True
        except Exception as e:
            logger.debug("qdrant_named_probe_failed collection=%s err=%s", collection, e)
        self._named_cache[collection] = named
        return named
