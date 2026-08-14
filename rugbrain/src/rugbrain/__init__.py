"""RugBrain — hybrid retrieval RAG engine (public API).

This package is the standalone OSS extraction of the RAG stack that
historically lived at ``app/rag/`` in the private monorepo. The package
skeleton is in place; each public symbol below is a stub until the
corresponding module is ported (see ``MIGRATION.md`` for the source map).

Public API surface (target, mirrors the private ``app.rag`` surface):

- ``RAGService``, ``init_rag`` — async search/ingest facade
- ``SearchRequest``, ``SearchResponse``, ``SearchHit``
- ``IngestRequest``, ``IngestResult``, ``FeedbackRecord``
- ``HybridRetriever``, ``ContextBuilder``, ``QueryExpander``, ``RelevanceScorer``
- ``BatchIngestor``, ``QualityFilter``, ``RateLimiter``, ``DocumentProcessor``
- ``EmbeddingProvider``, ``QdrantStore``, ``get_qdrant_store``
"""

from __future__ import annotations

__version__ = "0.1.0"

# Names that will be re-exported from their ported modules.
# Each maps to the private monorepo source module it will be ported from.
_PENDING_EXPORTS: dict[str, str] = {
    # core
    "RAGService": "app/rag/service.py",
    "init_rag": "app/rag/service.py",
    "SearchRequest": "app/rag/models.py",
    "SearchResponse": "app/rag/models.py",
    "SearchHit": "app/rag/models.py",
    "IngestRequest": "app/rag/models.py",
    "IngestResult": "app/rag/models.py",
    "FeedbackRecord": "app/rag/models.py",
    "EmbeddingProvider": "app/rag/models.py",
    "COLLECTIONS": "app/rag/models.py",
    "HybridRetriever": "app/rag/retrieval.py",
    "ContextBuilder": "app/rag/retrieval.py",
    "ContextWindow": "app/rag/retrieval.py",
    "QueryExpander": "app/rag/retrieval.py",
    "RelevanceScorer": "app/rag/retrieval.py",
    "ScoredHit": "app/rag/retrieval.py",
    "build_context": "app/rag/retrieval.py",
    "expand_query": "app/rag/retrieval.py",
    "hybrid_search": "app/rag/retrieval.py",
    # ingest
    "BatchIngestor": "app/rag/ingestion.py",
    "DocumentProcessor": "app/rag/ingestion.py",
    "QualityFilter": "app/rag/ingestion.py",
    "RateLimiter": "app/rag/ingestion.py",
    "IngestionScheduler": "app/rag/ingestion.py",
    "IngestionStatus": "app/rag/ingestion.py",
    "IngestReport": "app/rag/ingestion.py",
    "get_ingestor": "app/rag/ingestion.py",
    "get_processor": "app/rag/ingestion.py",
    "get_rate_limiter": "app/rag/ingestion.py",
    "get_scheduler": "app/rag/ingestion.py",
    # storage
    "QdrantStore": "app/rag/qdrant_store.py",
    "get_qdrant_store": "app/rag/qdrant_store.py",
}

__all__ = sorted(_PENDING_EXPORTS)

_IMPORTED: dict[str, object] = {}


def __getattr__(name: str) -> object:
    """Lazily expose the public API once modules are ported.

    Until a module is ported, accessing any public name raises
    NotImplementedError with a pointer to the private source module.
    """
    if name in _IMPORTED:
        return _IMPORTED[name]
    src = _PENDING_EXPORTS.get(name)
    if src is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    raise NotImplementedError(
        f"rugbrain.{name} is not ported yet — scaffold stub. "
        f"Port {src} into this package first (see MIGRATION.md)."
    )


def __dir__() -> list[str]:
    return __all__
