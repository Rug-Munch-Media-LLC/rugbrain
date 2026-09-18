"""QdrantVectorStore — composition of core + search/hybrid/write mixins."""

from __future__ import annotations

from rugbrain.qdrant_store.store_core import QdrantVectorStoreCore
from rugbrain.qdrant_store.store_hybrid import HybridSearchMixin
from rugbrain.qdrant_store.store_search import SearchMixin
from rugbrain.qdrant_store.store_write import WriteMixin

__all__ = ["QdrantVectorStore"]


class QdrantVectorStore(QdrantVectorStoreCore, SearchMixin, HybridSearchMixin, WriteMixin):
    """Lightweight Qdrant wrapper matching the SupabaseVectorStore interface.

    Methods exposed:
      - search(query_embedding, collection, limit, min_similarity)
      - hybrid_search(query_text, query_embedding, collection, limit) —
        dense + BM25 sparse → native Qdrant RRF fusion when the collection
        carries a ``bm25`` sparse vector, else dense + keyword re-rank
      - insert(doc_id, collection, embedding, content, metadata, source, severity)
    """
