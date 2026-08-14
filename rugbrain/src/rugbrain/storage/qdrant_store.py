"""Unified Qdrant vector store for the RAG stack — hybrid dense + sparse.

The primary retrieval backend for ``three_pillar_search`` (``app/rag/engine.py``)
behind the ``RAG_STORE=qdrant`` flag. Replaces the in-process numpy ANN
(``ann_index.py``) + Redis BM25-lite scan (``engine._keyword_search``) with a
single Qdrant hybrid query: dense (bge-m3) and sparse (lexical) prefetches
fused server-side with Reciprocal Rank Fusion (RRF, k=60 — Qdrant's native
default, matching the legacy ``_RRF_K = 60``).

Design notes
------------
- Uses ``AsyncQdrantClient`` (async variant of the official client) so callers
  in the FastAPI event loop never block on network I/O. No network I/O at
  import time — the client is created lazily on first use.
- Collections are **named-vector** collections (``{"dense": ..., "sparse": ...}``),
  matching the package's named-vector convention. The
  store probes each collection's config once per process and transparently
  falls back to dense-only search for legacy bare/named-dense collections
  (older bare/named-dense collections) that have no
  sparse vector configured.
- Stable point IDs: ``uuid5(NAMESPACE_URL, doc_id)`` so ``upsert`` is
  idempotent (same doc_id → same point → overwrite).
- Fail-open: any Qdrant error returns ``[]`` / ``False`` — never raises into
  the caller (the engine then falls back to the legacy pillar path).
- Env-driven config: ``RAG_QDRANT_URL`` (default ``settings.qdrant_url`` →
  ``http://127.0.0.1:6333``) and ``RAG_QDRANT_COLLECTION`` (default
  ``"rag_documents"``).

No secrets, no RPC URLs. Qdrant URL comes from env with the same
Docker-compose default as the rest of the backend.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from rugbrain._logging import get_logger
from rugbrain.embed.embeddings import EMBEDDING_DIM, get_embedding
from rugbrain.settings import settings

log = get_logger(__name__)

# Named vector names (package collection convention).
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

# RRF constant. Qdrant's server-side fusion uses k=60 (not client-tunable);
# kept here for parity with the legacy engine ``_RRF_K = 60`` and so the
# prefetch fan-out can be documented next to it.
RRF_K = 60

# Each pillar fetches this many candidates before fusion (legacy engine used
# top_k * 3 per pillar).
PREFETCH_MULTIPLIER = 3

# Per-collection store cache (one AsyncQdrantClient per collection name).
_store_cache: dict[str, QdrantStore] = {}


def _resolve_url() -> str:
    """Env-driven Qdrant URL: RAG_QDRANT_URL → settings.qdrant_url → 127.0.0.1:6333."""
    env = os.getenv("RAG_QDRANT_URL", "").strip().rstrip("/")
    if env:
        return env
    base = (getattr(settings, "qdrant_url", "") or "").rstrip("/")
    return base or "http://127.0.0.1:6333"


def _resolve_collection() -> str:
    """Env-driven collection name (default ``rag_documents``)."""
    return os.getenv("RAG_QDRANT_COLLECTION", "rag_documents").strip() or "rag_documents"


def _normalize_sparse(
    sparse_vector: models.SparseVector | dict[str, Any] | None,
) -> models.SparseVector | None:
    """Accept ``models.SparseVector`` or ``{"indices": [...], "values": [...]}``."""
    if sparse_vector is None:
        return None
    if isinstance(sparse_vector, models.SparseVector):
        return sparse_vector
    indices = sparse_vector.get("indices") or []
    values = sparse_vector.get("values") or []
    if not indices or not values or len(indices) != len(values):
        return None
    return models.SparseVector(indices=[int(i) for i in indices], values=[float(v) for v in values])


def _build_filter(filters: dict[str, Any] | None) -> models.Filter | None:
    """Build a Qdrant payload filter from a ``{key: value}`` dict.

    Exact-match (``MatchValue``) per key; list/tuple/set values become
    ``MatchAny``. ``None``/empty dict → no filter. Keys address payload
    fields (e.g. top-level ``collection``/``source`` or nested
    ``metadata.chain``).
    """
    if not filters:
        return None
    conditions: list[models.FieldCondition] = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            any_values = [v for v in value if v is not None]
            if any_values:
                conditions.append(models.FieldCondition(key=key, match=models.MatchAny(any=any_values)))
        else:
            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
    if not conditions:
        return None
    return models.Filter(must=conditions)  # type: ignore[arg-type]  # qdrant 1.19 union variance


def _point_to_hit(point: Any) -> dict[str, Any]:
    """Convert a Qdrant ScoredPoint into the engine's hit dict shape.

    Mirrors ``engine._qdrant_search`` payload conventions: ``content`` /
    ``text`` for the body and a ``metadata`` object (``source`` promoted into
    metadata when present at the top level).
    """
    payload = getattr(point, "payload", None) or {}
    metadata = dict(payload.get("metadata") or {})
    source = payload.get("source", "")
    if source and "source" not in metadata:
        metadata["source"] = source
    return {
        "doc_id": str(getattr(point, "id", "")),
        "score": float(getattr(point, "score", 0.0) or 0.0),
        "text": payload.get("content") or payload.get("text") or "",
        "metadata": metadata,
    }


class QdrantStore:
    """Unified Qdrant hybrid (dense + sparse) vector store for one collection.

    Example
    -------
    >>> store = get_qdrant_store()
    >>> await store.upsert("doc-1", vec, {"indices": [5], "values": [0.7]}, {"content": "..."})
    >>> hits = await store.search(vec, {"indices": [5], "values": [0.7]}, {"chain": "ethereum"}, limit=10)
    >>> await store.count()
    >>> await store.delete("doc-1")
    """

    def __init__(
        self,
        url: str | None = None,
        collection_name: str | None = None,
        vector_size: int | None = None,
        rrf_k: int = RRF_K,
        timeout: float = 10.0,
    ) -> None:
        self.url = (url or _resolve_url()).rstrip("/")
        self.collection_name = collection_name or _resolve_collection()
        self.vector_size = int(vector_size or EMBEDDING_DIM)
        self.rrf_k = rrf_k
        self._timeout = timeout
        self._client: AsyncQdrantClient | None = None
        self._config_probed = False
        self._has_named_dense = True  # assume named until probed
        self._has_sparse = False
        self._collection_ensured = False

    # ── client lifecycle ──────────────────────────────────────────────────
    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(url=self.url, timeout=int(self._timeout))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:  # pragma: no cover - close is best-effort
                log.debug("qdrant_store_close_error err=%s", exc)
            self._client = None

    async def __aenter__(self) -> QdrantStore:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ── point ids / collection setup ──────────────────────────────────────
    @staticmethod
    def point_id(doc_id: str) -> str:
        """Stable UUID5 point id for a doc_id (idempotent overwrite)."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))

    async def ensure_collection(self) -> bool:
        """Create the collection (named dense + sparse) once per process if missing.

        Fail-open: any probe/creation failure returns False — never raises.
        """
        if self._collection_ensured:
            return True
        try:
            if not await self.client.collection_exists(self.collection_name):
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={
                        DENSE_VECTOR_NAME: models.VectorParams(
                            size=self.vector_size,
                            distance=models.Distance.COSINE,
                        )
                    },
                    sparse_vectors_config={SPARSE_VECTOR_NAME: models.SparseVectorParams()},
                )
                log.info(
                    "qdrant_store_collection_created name=%s dim=%d sparse=%s",
                    self.collection_name,
                    self.vector_size,
                    SPARSE_VECTOR_NAME,
                )
        except Exception as exc:
            log.warning(
                "qdrant_store_collection_unavailable name=%s err=%s",
                self.collection_name,
                exc,
            )
            return False
        self._collection_ensured = True
        return True

    async def _probe_config(self) -> None:
        """Detect named-vs-bare dense and sparse support (once per instance).

        Legacy collections are bare-vector or named-dense. Only collections
        created by ``ensure_collection`` (or the future hybrid ingest path)
        carry the ``sparse`` named vector. On probe failure, fail-safe to
        named-dense + no sparse so dense search still works.
        """
        if self._config_probed:
            return
        try:
            info = await self.client.get_collection(self.collection_name)
            vectors = info.config.params.vectors
            sparse_config = info.config.params.sparse_vectors or {}
            if isinstance(vectors, dict):
                self._has_named_dense = DENSE_VECTOR_NAME in vectors
                # Sparse vectors live in their own config field
                # (``params.sparse_vectors``), not in the dense vectors dict.
                self._has_sparse = bool(sparse_config) and SPARSE_VECTOR_NAME in sparse_config
            else:
                # Bare single unnamed vector (legacy collections).
                self._has_named_dense = False
                self._has_sparse = False
        except Exception as exc:
            log.debug("qdrant_store_probe_failed name=%s err=%s", self.collection_name, exc)
            self._has_named_dense = True
            self._has_sparse = False
        self._config_probed = True

    # ── writes ────────────────────────────────────────────────────────────
    async def upsert(
        self,
        doc_id: str,
        vector: list[float],
        sparse_vector: models.SparseVector | dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Upsert one point (idempotent — stable uuid5 point id, overwrite).

        ``payload`` is stored verbatim; the caller controls whether the body
        lives under ``content``/``text`` and metadata under ``metadata`` (the
        convention the engine's search path reads).
        """
        pid = self.point_id(doc_id)
        sparse = _normalize_sparse(sparse_vector)
        try:
            if not await self.ensure_collection():
                return False
            point_vector: Any = {
                DENSE_VECTOR_NAME: [float(v) for v in vector],
            }
            if sparse is not None:
                point_vector[SPARSE_VECTOR_NAME] = sparse
            await self.client.upsert(
                collection_name=self.collection_name,
                points=[models.PointStruct(id=pid, vector=point_vector, payload=payload or {})],
            )
            return True
        except Exception as exc:
            log.warning(
                "qdrant_store_upsert_failed collection=%s doc_id=%s err=%s",
                self.collection_name,
                doc_id,
                exc,
            )
            return False

    # ── reads ─────────────────────────────────────────────────────────────
    async def search(
        self,
        vector: list[float],
        sparse_vector: models.SparseVector | dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Hybrid dense + sparse search with RRF fusion and payload filters.

        - Named-vector hybrid collection: two prefetches (dense + sparse),
          each ``limit * PREFETCH_MULTIPLIER`` candidates, fused server-side
          with ``Fusion.RRF`` (k=60).
        - Dense-only collection (no sparse vector configured): plain dense
          query with the payload filter applied.
        - Bare-vector collection: dense query without a vector name.

        Returns hit dicts ``{doc_id, score, text, metadata}`` (same shape as
        the engine's ``Hit``) or ``[]`` on any error (fail-open).
        """
        await self._probe_config()
        qfilter = _build_filter(filters)
        prefetch_limit = max(limit, 1) * PREFETCH_MULTIPLIER
        sparse = _normalize_sparse(sparse_vector)

        prefetch: list[models.Prefetch] = []
        if vector:
            # NOTE: qdrant-client 1.18 rejects models.NamedVector inside
            # Prefetch.query — pass the plain vector and select the named
            # vector via `using` (verified against a live 1.18 server).
            dense: list[float] = [float(v) for v in vector]
            if self._has_named_dense:
                prefetch.append(
                    models.Prefetch(
                        query=dense,
                        using=DENSE_VECTOR_NAME,
                        limit=prefetch_limit,
                        filter=qfilter,
                    )
                )
            else:
                prefetch.append(
                    models.Prefetch(
                        query=dense,
                        limit=prefetch_limit,
                        filter=qfilter,
                    )
                )
        if self._has_sparse and sparse is not None:
            prefetch.append(
                models.Prefetch(
                    query=sparse,
                    using=SPARSE_VECTOR_NAME,
                    limit=prefetch_limit,
                    filter=qfilter,
                )
            )

        if not prefetch:
            return []

        try:
            if len(prefetch) > 1:
                resp = await self.client.query_points(
                    collection_name=self.collection_name,
                    prefetch=prefetch,
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    limit=limit,
                    with_payload=True,
                )
            else:
                only = prefetch[0]
                resp = await self.client.query_points(
                    collection_name=self.collection_name,
                    query=only.query,
                    using=only.using,
                    query_filter=qfilter,
                    limit=limit,
                    with_payload=True,
                )
        except Exception as exc:
            log.warning(
                "qdrant_store_search_failed collection=%s err=%s",
                self.collection_name,
                exc,
            )
            return []

        return [_point_to_hit(p) for p in resp.points]

    async def delete(self, doc_id: str) -> bool:
        """Delete the point for *doc_id* (stable id). False when missing/error."""
        pid = self.point_id(doc_id)
        try:
            await self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=[pid]),
            )
            return True
        except Exception as exc:
            log.warning(
                "qdrant_store_delete_failed collection=%s doc_id=%s err=%s",
                self.collection_name,
                doc_id,
                exc,
            )
            return False

    async def count(self) -> int:
        """Return the number of points in the collection (0 on error)."""
        try:
            result = await self.client.count(collection_name=self.collection_name, exact=True)
            return int(getattr(result, "count", 0) or 0)
        except Exception as exc:
            log.warning(
                "qdrant_store_count_failed collection=%s err=%s",
                self.collection_name,
                exc,
            )
            return 0


def get_qdrant_store(collection: str | None = None) -> QdrantStore:
    """Factory: cached ``QdrantStore`` per collection name (lazy client)."""
    name = collection or _resolve_collection()
    if name not in _store_cache:
        _store_cache[name] = QdrantStore(collection_name=name)
    return _store_cache[name]


# ── Ingest / write path (consolidation Phase 1) ───────────────────────
def build_sparse_vector(text: str) -> dict[str, Any] | None:
    """Lexical sparse vector (``{"indices": [...], "values": [...]}``) for a doc.

    Reuses ``engine._build_sparse_query`` (same stopword set + sha256
    token→32-bit-index hashing) so ingest-side sparse terms match the
    query-side sparse terms exactly — Qdrant matches sparse vectors on
    token index, so both sides must tokenize identically. Returns None for
    empty/stopword-only text (caller upserts dense-only).
    """
    # Lazy import: engine.py imports this module only inside functions, so
    # a deferred import here cannot create a module-load cycle.
    from rugbrain.core.engine import _build_sparse_query

    return _build_sparse_query(text)


async def upsert_rag_document(
    doc_id: str,
    text: str,
    payload: dict[str, Any] | None = None,
    *,
    collection: str | None = None,
    vector: list[float] | None = None,
) -> bool:
    """Embed + upsert one document into the unified Qdrant store.

    Primary Qdrant write path (QDRANT-CONSOLIDATION Phase 1). Given a doc
    (``doc_id``, ``text``, ``payload``):

      - embeds *text* via the existing Infinity bge-m3 embedder
        (``app.rag.embeddings.get_embedding``) unless a precomputed
        *vector* is passed (test seam);
      - builds a lexical sparse vector with ``build_sparse_vector``
        (dense-only upsert when the text is stopword-only);
      - upserts into the store's collection — created on first use via
        ``QdrantStore.ensure_collection`` (named dense 1024d cosine +
        named sparse ``sparse``).

    Fail-open: any embedder/store error returns False and never raises —
    callers keep their legacy numpy/Redis writes as the fallback path
    (additive write, no removal).
    """
    store = get_qdrant_store(collection)
    try:
        if vector is None:
            vector = await get_embedding(text)
        sparse = build_sparse_vector(text)
        return await store.upsert(doc_id, vector, sparse_vector=sparse, payload=payload)
    except Exception as exc:
        log.warning(
            "rag_upsert_document_failed collection=%s doc_id=%s err=%s",
            store.collection_name,
            doc_id,
            exc,
        )
        return False


__all__ = [
    "DENSE_VECTOR_NAME",
    "PREFETCH_MULTIPLIER",
    "RRF_K",
    "SPARSE_VECTOR_NAME",
    "QdrantStore",
    "build_sparse_vector",
    "get_qdrant_store",
    "upsert_rag_document",
]
