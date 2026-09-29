"""Qdrant vector store — lightweight wrapper over Qdrant REST API.

Replaces app.supabase_vector.SupabaseVectorStore with the same
search / hybrid_search / insert interface backed by Qdrant.

Write gate: RMI_RAG_QDRANT_WRITE_MODE env var (default: readonly).
  - readonly: rugbrain writes only to its own collections (rag_* + known owned).
  - owner:    full write access (legacy behaviour).

Package layout (split from the former single-module qdrant_store.py):
  connection          — URL resolution / health probe
  embeddings          — BM25 sparse embedder (fastembed)
  filters             — payload filter builders
  owned_registry      — the FACTS of write ownership (which names are ours)
  ownership           — the write GATE decision (canonical name -> owned?)
  namespace           — name routing: interim shadow, canonical lane, unified reads
  collection_schema   — parse dims out of a Qdrant vector config (no I/O)
  collection_lifecycle— probe/create a collection, the only Qdrant-I/O module
  collections         — compat facade re-exporting the five above
  lane_widths         — MEASURED lane widths, pinned as constants
  write_lane          — pick the lane that fits the vector in hand, or raise
  store_core          — client lifecycle + capability probes
  store_search        — dense search + RRF fusion
  store_hybrid        — hybrid_search with keyword re-rank fallback
  store_write         — insert / has_point / close
  store               — QdrantVectorStore composition

Monkeypatch compatibility: tests patch attributes on THIS package
(e.g. ``QdrantVectorStore``, ``_RMI_RAG_QDRANT_WRITE_MODE``). Internal
callers therefore resolve shared names through the package namespace at
call time (``import rugbrain.qdrant_store as _pkg``) so patches apply.
"""

from __future__ import annotations

from rugbrain.qdrant_store.collection_lifecycle import (
    _ensure_collection,
    _existing_collections,
)
from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions
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
from rugbrain.qdrant_store.lane_dims import fetch_live_dim
from rugbrain.qdrant_store.lane_resolver import (
    CANONICAL_LANE_DIM,
    EIGHT_B_SUFFIX,
    UnresolvedLane,
    declared_lane_dim,
    resolve_collection,
    resolver_mode,
    strip_twin_suffix,
)
from rugbrain.qdrant_store.namespace import (
    _INTERIM_SUFFIX,
    map_collection,
    unified_route,
)
from rugbrain.qdrant_store.owned_registry import (
    _RMI_RAG_OWNED_COLLECTIONS,
    _RMI_RAG_QDRANT_WRITE_MODE,
)
from rugbrain.qdrant_store.ownership import gate_collection_name
from rugbrain.qdrant_store.store import QdrantVectorStore

#: Historical alias: the pre-split name of :func:`map_collection`.
_map_collection = map_collection

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
    "CANONICAL_LANE_DIM",
    "DENSE_VECTOR_NAME",
    "EIGHT_B_SUFFIX",
    "FILTERABLE_MATCH_FIELDS",
    "FILTERABLE_RANGE_FIELDS",
    "QDRANT_FALLBACK_URLS",
    "QDRANT_URL",
    "SPARSE_VECTOR_NAME",
    "_INTERIM_SUFFIX",
    "_RMI_RAG_OWNED_COLLECTIONS",
    "_RMI_RAG_QDRANT_WRITE_MODE",
    "QdrantVectorStore",
    "UnresolvedLane",
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
    "declared_lane_dim",
    "dense_vector_dimensions",
    "fetch_live_dim",
    "gate_collection_name",
    "get_qdrant_store",
    "normalize_filters",
    "resolve_collection",
    "resolver_mode",
    "strip_twin_suffix",
    "unified_route",
]
