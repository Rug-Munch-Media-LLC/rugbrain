"""Qdrant vector store — lightweight wrapper over Qdrant REST API.

Replaces app.supabase_vector.SupabaseVectorStore with the same
search / hybrid_search / insert interface backed by Qdrant.

Write gate: RMI_RAG_QDRANT_WRITE_MODE env var (default: readonly).
  - readonly: rugbrain writes only to its own collections (rag_* + known owned).
  - owner:    full write access (legacy behaviour).

Package layout (split from the former single-module qdrant_store.py):
  connection   — URL resolution / health probe
  embeddings   — BM25 sparse embedder (fastembed)
  filters      — payload filter builders
  collections  — collection registry, write gate, interim namespace
  store_core   — client lifecycle + capability probes
  store_search — dense search + RRF fusion
  store_hybrid — hybrid_search with keyword re-rank fallback
  store_write  — insert / has_point / close
  store        — QdrantVectorStore composition

Monkeypatch compatibility: tests patch attributes on THIS package
(e.g. ``QdrantVectorStore``, ``_RMI_RAG_QDRANT_WRITE_MODE``). Internal
callers therefore resolve shared names through the package namespace at
call time (``import rugbrain.qdrant_store as _pkg``) so patches apply.
"""

from __future__ import annotations

from rugbrain.qdrant_store.collections import (
    _INTERIM_SUFFIX,
    _RMI_RAG_OWNED_COLLECTIONS,
    _RMI_RAG_QDRANT_WRITE_MODE,
    _ensure_collection,
    _existing_collections,
    _map_collection,
    dense_vector_dimensions,
    unified_route,
)
from rugbrain.qdrant_store.connection import (
    QDRANT_FALLBACK_URLS,
    QDRANT_URL,
    _probe_qdrant,
    _resolve_qdrant_url,
)
from rugbrain.qdrant_store.embeddings import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    _embed_sparse,
    _get_bm25_embedder,
)
from rugbrain.qdrant_store.filters import (
    FILTERABLE_MATCH_FIELDS,
    FILTERABLE_RANGE_FIELDS,
    apply_payload_filter,
    build_qdrant_filter,
    build_search_filters,
    normalize_filters,
)
from rugbrain.qdrant_store.store import QdrantVectorStore

_store: QdrantVectorStore | None = None


async def get_qdrant_store() -> QdrantVectorStore:
    """Return the process-wide ``QdrantVectorStore`` singleton.

    Resolves the class through this package's namespace at call time so
    tests that monkeypatch ``rugbrain.qdrant_store.QdrantVectorStore``
    keep working.
    """
    global _store
    if _store is None:
        cls: type[QdrantVectorStore] = QdrantVectorStore
        import rugbrain.qdrant_store as _pkg

        cls = getattr(_pkg, "QdrantVectorStore", QdrantVectorStore)
        _store = cls()
    return _store


__all__ = [
    "DENSE_VECTOR_NAME",
    "FILTERABLE_MATCH_FIELDS",
    "FILTERABLE_RANGE_FIELDS",
    "QDRANT_FALLBACK_URLS",
    "QDRANT_URL",
    "SPARSE_VECTOR_NAME",
    "_INTERIM_SUFFIX",
    "_RMI_RAG_OWNED_COLLECTIONS",
    "_RMI_RAG_QDRANT_WRITE_MODE",
    "QdrantVectorStore",
    "_embed_sparse",
    "_ensure_collection",
    "_existing_collections",
    "_get_bm25_embedder",
    "_map_collection",
    "_probe_qdrant",
    "_resolve_qdrant_url",
    "_store",
    "apply_payload_filter",
    "build_qdrant_filter",
    "build_search_filters",
    "dense_vector_dimensions",
    "get_qdrant_store",
    "normalize_filters",
    "unified_route",
]
