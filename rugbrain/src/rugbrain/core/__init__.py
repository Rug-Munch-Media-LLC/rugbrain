"""Core retrieval, ingestion support, evaluation and service layers."""

from __future__ import annotations

from rugbrain.core.chunking import (
    Chunk,
    chunk_text,
    content_hash,
    estimate_tokens,
    is_duplicate,
    mark_ingested,
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
from rugbrain.core.engine import bulk_ingest, get_stats, ingest_document, three_pillar_search
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
    FeedbackRecord,
    IngestRequest,
    IngestResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from rugbrain.core.quality import QualityFilter, quality_score
from rugbrain.core.rerank import reorder_by_relevance, rerank_results
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
from rugbrain.core.service import RAGService, init_rag

__all__ = [
    "COLLECTIONS",
    "Chunk",
    "ContextBuilder",
    "ContextWindow",
    "FeedbackRecord",
    "HybridRetriever",
    "IngestRequest",
    "IngestResult",
    "QualityFilter",
    "QueryExpander",
    "RAGService",
    "RelevanceScorer",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "ScoredHit",
    "build_context",
    "bulk_ingest",
    "chunk_text",
    "content_fingerprint",
    "content_hash",
    "estimate_tokens",
    "evaluate_on_golden_set",
    "evaluate_rag_pipeline",
    "expand_query",
    "extract_crypto_entities",
    "get_evaluation_history",
    "get_feedback",
    "get_kg_provider",
    "get_stats",
    "ingest_document",
    "init_rag",
    "is_duplicate",
    "is_duplicate_content",
    "is_duplicate_url",
    "mark_content_ingested",
    "mark_ingested",
    "mark_url_ingested",
    "near_duplicate",
    "new_group_id",
    "normalize_address",
    "quality_score",
    "record_evaluation_results",
    "record_feedback",
    "reorder_by_relevance",
    "rerank_results",
    "set_kg_provider",
    "thresholds_met",
    "three_pillar_search",
]
