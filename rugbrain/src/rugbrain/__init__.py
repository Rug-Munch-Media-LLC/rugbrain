"""RugBrain — hybrid retrieval RAG engine (public API).

RugBrain is a self-hostable hybrid-retrieval RAG engine: dense vector
search (bge-m3 served over an Infinity-compatible HTTP endpoint) fused
with a lightweight sparse/BM25-style signal via reciprocal-rank fusion
(RRF), optional cross-encoder reranking, idempotent ingestion
(chunking → dedup → quality gate → upsert), and fail-open behaviour for
every external dependency.

This package is the standalone extraction of the RAG stack that
historically lived at ``app/rag/`` in the private monorepo; all internal
coupling (``app.core.*``, ``app.config``, private service clients) has
been replaced with env-driven settings and boundary protocols
(:mod:`rugbrain.providers`). See ``PORTING-NOTES.md`` for what shipped
and what stayed private.

Public API surface:

- Search/ingest facade: ``RAGService``, ``init_rag``
- Wire models: ``SearchRequest``, ``SearchResponse``, ``SearchHit``,
  ``IngestRequest``, ``IngestResult``, ``FeedbackRecord``
- Retrieval: ``HybridRetriever``, ``ContextBuilder``, ``ContextWindow``,
  ``QueryExpander``, ``RelevanceScorer``, ``ScoredHit``
- Ingestion: ``BatchIngestor``, ``IngestionScheduler``, ``DocumentProcessor``,
  ``QualityFilter``, ``RateLimiter``, ``IngestReport``, ``IngestionStatus``
- Storage/embedding: ``QdrantStore``, ``get_qdrant_store``, ``get_cache``,
  ``get_embedding``, ``get_embedder``, ``EmbedderProtocol``
- Engine: ``three_pillar_search``, ``ingest_document``, ``get_stats``
- Eval/observability: ``evaluate_on_golden_set``, ``setup_phoenix``,
  ``trace_span``
- Protocols (injectable seams): ``ScannerClient``, ``BalancesClient``,
  ``KGProvider``, ``CrawlerProvider``, ``NewsProvider``, ``LLMRouter``,
  ``CacheBackend``, ``Reranker``
"""

from __future__ import annotations

__version__ = "0.1.0"

# ── Core wire models ────────────────────────────────────────────────────
# ── Chunking / dedup ────────────────────────────────────────────────────
from rugbrain.core.chunking import (
    chunk_text,
    estimate_tokens,
)
from rugbrain.core.chunking import (
    content_hash as chunk_content_hash,
)
from rugbrain.core.chunking import (
    is_duplicate as is_chunk_duplicate,
)
from rugbrain.core.chunking import (
    mark_ingested as mark_chunk_ingested,
)
from rugbrain.core.dedup import (
    content_fingerprint,
    extract_crypto_entities,
    is_duplicate_content,
    is_duplicate_url,
    mark_content_ingested,
    mark_url_ingested,
    near_duplicate,
    new_group_id,
    normalize_address,
)

# ── Engine ──────────────────────────────────────────────────────────────
from rugbrain.core.engine import (
    bulk_ingest,
    get_collection_stats,
    get_stats,
    ingest_document,
    three_pillar_search,
)

# ── Evaluation / feedback ───────────────────────────────────────────────
from rugbrain.core.evaluation import (
    evaluate_on_golden_set,
    evaluate_rag_pipeline,
    get_evaluation_history,
    record_evaluation_results,
    thresholds_met,
)
from rugbrain.core.feedback import get_feedback, record_feedback
from rugbrain.core.models import (
    COLLECTIONS,
    EmbeddingProvider,
    FeedbackRecord,
    IngestRequest,
    IngestResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

# ── Ingestion ───────────────────────────────────────────────────────────
from rugbrain.core.quality import QualityFilter, quality_score

# ── Rerank / tracing / settings / providers ────────────────────────────
from rugbrain.core.rerank import reorder_by_relevance, rerank_results

# ── Retrieval ───────────────────────────────────────────────────────────
from rugbrain.core.retrieval import (
    ContextBuilder,
    ContextWindow,
    HybridRetriever,
    QueryExpander,
    RelevanceScorer,
    ScoredHit,
    build_context,
    expand_query,
    get_kg_provider,
    set_kg_provider,
)

# ── Service facade ──────────────────────────────────────────────────────
from rugbrain.core.service import RAGService, init_rag

# ── Embedding ───────────────────────────────────────────────────────────
from rugbrain.embed.embedder import (
    BGE_M3_Embedder,
    CachedEmbedder,
    EmbedderProtocol,
    FallbackEmbedder,
    InfinityEmbedder,
    get_default_embedder,
    get_embedder,
    lookup_dim,
    normalize_dim,
    shutdown_embedder,
)
from rugbrain.embed.embeddings import (
    EMBEDDING_DIM,
    RAGUnavailableError,
    create_embedder,
    current_backend,
    get_embedding,
    get_embeddings,
)
from rugbrain.ingest.ingestion import (
    BatchIngestor,
    IngestionScheduler,
    get_ingestor,
    get_processor,
    get_rate_limiter,
    get_scheduler,
)
from rugbrain.ingest.ingestion_models import Chunk, Document, IngestionStatus, IngestReport
from rugbrain.ingest.processor import DocumentProcessor
from rugbrain.ingest.rate_limiter import RateLimiter
from rugbrain.observability.tracing import is_phoenix_enabled, setup_phoenix, trace_span
from rugbrain.providers import (
    BalancesClient,
    CrawlerProvider,
    KGProvider,
    LLMRouter,
    NewsProvider,
    Reranker,
    ScannerClient,
)
from rugbrain.settings import Settings, get_settings, settings

# ── Storage ─────────────────────────────────────────────────────────────
from rugbrain.storage.ann_index import ANNIndex, Hit, get_index
from rugbrain.storage.cache import CacheBackend, LocalCache, RedisCache, get_cache
from rugbrain.storage.qdrant_store import QdrantStore, get_qdrant_store

__all__ = [
    "ANNIndex",
    "BGE_M3_Embedder",
    "BalancesClient",
    "BatchIngestor",
    "COLLECTIONS",
    "CacheBackend",
    "CachedEmbedder",
    "Chunk",
    "ContextBuilder",
    "ContextWindow",
    "CrawlerProvider",
    "Document",
    "DocumentProcessor",
    "EMBEDDING_DIM",
    "EmbeddingProvider",
    "EmbedderProtocol",
    "FallbackEmbedder",
    "FeedbackRecord",
    "Hit",
    "HybridRetriever",
    "InfinityEmbedder",
    "IngestReport",
    "IngestRequest",
    "IngestResult",
    "IngestionScheduler",
    "IngestionStatus",
    "KGProvider",
    "LLMRouter",
    "LocalCache",
    "NewsProvider",
    "QdrantStore",
    "QualityFilter",
    "QueryExpander",
    "RAGService",
    "RAGUnavailableError",
    "RateLimiter",
    "RedisCache",
    "RelevanceScorer",
    "Reranker",
    "ScannerClient",
    "ScoredHit",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "Settings",
    "bulk_ingest",
    "build_context",
    "chunk_content_hash",
    "chunk_text",
    "content_fingerprint",
    "create_embedder",
    "current_backend",
    "estimate_tokens",
    "evaluate_on_golden_set",
    "evaluate_rag_pipeline",
    "expand_query",
    "extract_crypto_entities",
    "get_cache",
    "get_collection_stats",
    "get_default_embedder",
    "get_evaluation_history",
    "get_embedder",
    "get_embedding",
    "get_embeddings",
    "get_feedback",
    "get_index",
    "get_ingestor",
    "get_kg_provider",
    "get_processor",
    "get_qdrant_store",
    "get_rate_limiter",
    "get_scheduler",
    "get_settings",
    "get_stats",
    "init_rag",
    "ingest_document",
    "is_chunk_duplicate",
    "is_duplicate_content",
    "is_duplicate_url",
    "is_phoenix_enabled",
    "lookup_dim",
    "mark_chunk_ingested",
    "mark_content_ingested",
    "mark_url_ingested",
    "near_duplicate",
    "new_group_id",
    "normalize_address",
    "normalize_dim",
    "quality_score",
    "record_evaluation_results",
    "record_feedback",
    "reorder_by_relevance",
    "rerank_results",
    "set_kg_provider",
    "settings",
    "setup_phoenix",
    "shutdown_embedder",
    "thresholds_met",
    "three_pillar_search",
    "trace_span",
]
