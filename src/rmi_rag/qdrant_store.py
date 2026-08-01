"""Qdrant vector store — lightweight wrapper over Qdrant REST API.

Replaces app.supabase_vector.SupabaseVectorStore with the same
search / hybrid_search / insert interface backed by Qdrant.

Connection strategy:
  1. Use QDRANT_URL env var (default: http://rmi-qdrant:6333 for Docker)
  2. Fallback to http://127.0.0.1:{QDRANT_PORT or 6333} for local dev
  3. Fallback to http://localhost:{QDRANT_PORT or 6333}
  First URL that responds to GET / is used for the session.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Connection resolution ──────────────────────────────────────────────

QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333")
QDRANT_FALLBACK_URLS: list[str] = []

# Build fallback chain: primary → loopback variants
_primary = QDRANT_URL.rstrip("/")
_fallback_port = os.getenv("QDRANT_PORT", "6333")
QDRANT_FALLBACK_URLS = [
    _primary,
    f"http://127.0.0.1:{_fallback_port}",
    f"http://localhost:{_fallback_port}",
]
# Deduplicate while preserving order
_seen: set[str] = set()
QDRANT_FALLBACK_URLS = [
    u
    for u in QDRANT_FALLBACK_URLS
    if u not in _seen and not _seen.add(u)  # type: ignore[func-returns-value]
]

_resolved_url: str | None = None
_resolved_lock = asyncio.Lock()


async def _resolve_qdrant_url() -> str:
    """Probe fallback URLs and return the first responsive one.

    Caches the resolved URL so we only probe once per process lifetime.
    """
    global _resolved_url
    if _resolved_url is not None:
        return _resolved_url

    async with _resolved_lock:
        if _resolved_url is not None:
            return _resolved_url  # double-check after acquiring lock

        for url in QDRANT_FALLBACK_URLS:
            try:
                async with httpx.AsyncClient(base_url=url, timeout=3.0) as c:
                    r = await c.get("/")
                    if r.status_code == 200:
                        _resolved_url = url
                        logger.info("qdrant_resolved url=%s", url)
                        return url
            except Exception:
                logger.debug("qdrant_probe_failed url=%s", url)
                continue

        # None worked — use primary anyway; callers will handle errors
        _resolved_url = _primary
        logger.warning("qdrant_unreachable using_fallback=%s", _resolved_url)
        return _resolved_url


async def _probe_qdrant() -> bool:
    """Simple boolean health probe — used by /health endpoint."""
    try:
        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=3.0) as c:
            r = await c.get("/")
            return r.status_code == 200
    except Exception:
        return False


# ── BM25 sparse embeddings (fastembed) ──────────────────────────────

# Name of the sparse vector in hybrid collections. Qdrant collection
# configs are immutable, so sparse support is provisioned by creating
# ``{name}_v2`` collections (see scripts/migrate_to_sparse.py) and
# pointing the original name at them via an alias.
SPARSE_VECTOR_NAME = "bm25"

_bm25_embedder: Any = None
_bm25_embedder_lock = threading.Lock()


def _get_bm25_embedder() -> Any | None:
    """Lazily load the fastembed ``Qdrant/bm25`` sparse embedder.

    Returns None when fastembed is unavailable or the model fails to
    load so callers can degrade to dense-only behaviour instead of
    crashing the ingest/search path.
    """
    global _bm25_embedder
    if _bm25_embedder is not None:
        return _bm25_embedder if _bm25_embedder is not False else None
    with _bm25_embedder_lock:
        try:
            from fastembed import SparseTextEmbedding

            _bm25_embedder = SparseTextEmbedding(model_name="Qdrant/bm25")
            logger.info("bm25_embedder_loaded model=Qdrant/bm25")
        except Exception as e:
            logger.warning("bm25_embedder_unavailable err=%s", e)
            _bm25_embedder = False
    return _bm25_embedder if _bm25_embedder is not False else None


async def _embed_sparse(text: str) -> tuple[list[int], list[float]] | None:
    """Compute a BM25 sparse vector ``(indices, values)`` for *text*.

    Returns None when *text* is empty or the BM25 embedder is not
    available (callers fall back to dense-only vectors).
    """
    if not text or not text.strip():
        return None
    embedder = _get_bm25_embedder()
    if embedder is None:
        return None
    try:
        sparse = next(iter(embedder.embed([text])))
        return (sparse.indices.tolist(), [float(v) for v in sparse.values.tolist()])
    except Exception as e:
        logger.warning("bm25_embed_error: %s", e)
        return None


# ── Collection management ─────────────────────────────────────────────

_existing_collections: set[str] = set()


async def _ensure_collection(collection: str, vector_size: int, client: httpx.AsyncClient) -> bool:
    if collection in _existing_collections:
        return True
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code == 200:
            _existing_collections.add(collection)
            return True
    except Exception as e:
        logger.debug("qdrant_collection_probe_failed collection=%s err=%s", collection, e)
    try:
        payload: dict[str, Any] = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        r = await client.put(f"/collections/{collection}", json=payload)
        ok = r.status_code in (200, 201)
        if ok:
            _existing_collections.add(collection)
        return ok
    except Exception as e:
        logger.warning("qdrant_create_collection_failed collection=%s err=%s", collection, e)
        return False


class QdrantVectorStore:
    """Lightweight Qdrant wrapper matching the SupabaseVectorStore interface.

    Methods exposed:
      - search(query_embedding, collection, limit, min_similarity)
      - hybrid_search(query_text, query_embedding, collection, limit) —
        dense + BM25 sparse → native Qdrant RRF fusion when the collection
        carries a ``bm25`` sparse vector, else dense + keyword re-rank
      - insert(doc_id, collection, embedding, content, metadata, source, severity)
    """

    def __init__(self, url: str | None = None):
        self._url = (url or QDRANT_URL).rstrip("/")
        self._client: httpx.AsyncClient | None = None
        # Per-instance cache of whether a collection carries a sparse
        # vector config (avoids a GET per insert/search in production).
        self._sparse_cache: dict[str, bool] = {}

    @property
    def url(self) -> str:
        return self._url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            resolved = await _resolve_qdrant_url()
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

    async def search(
        self,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.6,
        filters: dict | None = None,
    ) -> list[dict[str, Any]]:
        coll = collection or "default"
        client = await self._get_client()
        await _ensure_collection(coll, len(query_embedding), client)

        payload: dict[str, Any] = {
            "vector": query_embedding,
            "limit": limit,
            "score_threshold": min_similarity,
            "with_payload": True,
        }
        if filters:
            payload["filter"] = {
                "must": [{"key": k, "match": {"value": v}} for k, v in filters.items()]
            }

        try:
            r = await client.post(f"/collections/{coll}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning("qdrant_search_failed status=%d body=%s", r.status_code, r.text)
                return []
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "similarity": round(point.get("score", 0), 4),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            return results
        except Exception as e:
            logger.warning("qdrant_search_error: %s", e)
            return []

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        vector_weight: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Dense + BM25 sparse hybrid search with Reciprocal Rank Fusion.

        When the target collection carries the ``bm25`` sparse vector
        (post-migration) and the BM25 embedder is available, Qdrant fuses
        the dense and sparse result lists natively (``fusion="rrf"``).
        Otherwise it falls back to the dense-only search with lightweight
        keyword re-ranking so pre-migration collections keep working.
        """
        coll = collection or "default"
        client = await self._get_client()
        await _ensure_collection(coll, len(query_embedding), client)

        if query_text and await self._collection_supports_sparse(coll, client):
            sparse = await _embed_sparse(query_text)
            if sparse is not None:
                fused = await self._hybrid_search_rrf(client, coll, query_embedding, sparse, limit)
                if fused is not None:
                    return fused

        # ── Fallback: dense search + keyword re-rank (pre-sparse behaviour) ──
        results = await self.search(query_embedding, collection=coll, limit=limit * 2)

        if query_text and results:
            terms = set(query_text.lower().split())
            for r in results:
                content_lower = r.get("content", "").lower()
                kw_hits = sum(1 for t in terms if t in content_lower)
                keyword_score = kw_hits / max(1, len(terms))
                r["rrf_score"] = (
                    vector_weight * r["similarity"] + (1 - vector_weight) * keyword_score
                )
                r["match_type"] = "hybrid" if kw_hits > 0 else "vector"
            results.sort(
                key=lambda x: x.get("rrf_score", 0),
                reverse=True,  # type: ignore[arg-type,return-value]
            )
        else:
            for r in results:
                r["rrf_score"] = r["similarity"]
                r["match_type"] = "vector"

        final: list[dict[str, Any]] = []
        for r in results[:limit]:
            r.pop("rrf_score", None)
            r.pop("match_type", None)
            final.append(r)
        return final

    async def _hybrid_search_rrf(
        self,
        client: httpx.AsyncClient,
        collection: str,
        dense_vector: list[float],
        sparse: tuple[list[int], list[float]],
        limit: int,
    ) -> list[dict[str, Any]] | None:
        """Native Qdrant hybrid search: named dense + BM25 sparse, RRF fusion.

        Returns formatted results, or None when Qdrant rejects the request
        so the caller can fall back to dense-only search.
        """
        payload: dict[str, Any] = {
            "vector": {"name": "dense", "vector": dense_vector},
            "sparse_vector": {
                "name": SPARSE_VECTOR_NAME,
                "vector": {"indices": sparse[0], "values": sparse[1]},
            },
            "fusion": "rrf",
            "limit": limit,
            "with_payload": True,
        }
        try:
            r = await client.post(f"/collections/{collection}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning(
                    "qdrant_hybrid_rrf_failed status=%d body=%s",
                    r.status_code,
                    r.text,
                )
                return None
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "similarity": round(point.get("score", 0), 4),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            return results
        except Exception as e:
            logger.warning("qdrant_hybrid_rrf_error: %s", e)
            return None

    @staticmethod
    def _to_uuid(doc_id: str) -> str:
        """Convert a string doc_id to a deterministic UUID v5.

        Qdrant REST API requires point IDs to be unsigned integers or UUIDs.
        We use UUID v5 (SHA-1 namespace + name) for deterministic conversion.
        Namespace: 6ba7b810-9dad-11d1-80b4-00c04fd430c8 (DNS namespace)
        """
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))

    async def insert(
        self,
        doc_id: str,
        collection: str,
        embedding: list[float],
        content: str = "",
        metadata: dict | None = None,
        source: str = "",
        severity: str = "medium",
        chain: str = "",
    ) -> bool:
        """Insert a document into *collection*.

        When the target collection is configured with the ``bm25`` sparse
        vector (hybrid collections created by scripts/migrate_to_sparse.py),
        a BM25 sparse vector is computed from *content* and stored alongside
        the dense vector so hybrid (RRF) search can use it. Dense-only
        collections are written unchanged.
        """
        client = await self._get_client()
        await _ensure_collection(collection, len(embedding), client)

        point_id = self._to_uuid(doc_id)
        vector_field: Any = embedding
        if await self._collection_supports_sparse(collection, client):
            vector_field = {"dense": embedding}
            sparse = await _embed_sparse(content)
            if sparse is not None:
                vector_field[SPARSE_VECTOR_NAME] = {
                    "indices": sparse[0],
                    "values": sparse[1],
                }
        payload = {
            "points": [
                {
                    "id": point_id,
                    "vector": vector_field,
                    "payload": {
                        "content": content[:10000],
                        "metadata": metadata or {},
                        "source": source,
                        "severity": severity,
                        "chain": chain,
                        "collection": collection,
                    },
                }
            ]
        }
        try:
            r = await client.put(f"/collections/{collection}/points", json=payload)
            ok = r.status_code in (200, 201)
            if not ok:
                logger.warning(
                    "qdrant_insert_failed status=%d body=%s",
                    r.status_code,
                    r.text,
                )
            return ok
        except Exception as e:
            logger.warning("qdrant_insert_error: %s", e)
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


_store: QdrantVectorStore | None = None


async def get_qdrant_store() -> QdrantVectorStore:
    global _store
    if _store is None:
        _store = QdrantVectorStore()
    return _store
