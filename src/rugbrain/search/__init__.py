"""
RAG Search package — engine, ranking, filters.

Re-exports all public API from submodules for backward compatibility.
"""

# ── Submodules ──
# ── External re-exports (backward compat) ──
from rugbrain.crypto_embeddings import COLLECTIONS, KNOWN_SCAM_PATTERNS
from rugbrain.scam_detection import detect_scam_patterns
from rugbrain.seed_scams import seed_known_scams

from ._state import (
    _get_redis,
    _pattern_cache,
    _pattern_cache_loaded,
    _seeded,
)
from .engine import (
    CODING_COLLECTIONS,
    SCAM_COLLECTIONS,
    _enrich_ann_results,
    bias_toward_code_chunks,
    detect_coding_query,
    detect_scam_query,
    extract_code_blocks,
    format_code_answer,
    resolve_search_collections,
    search_coding_knowledge,
    search_multi_collection,
    search_scam_knowledge,
    search_similar,
    search_with_transform,
    three_pillar_search,
)
from .filters import (
    _embed_by_collection,
    _preembed_scam_patterns,
    get_goplus_analysis,
    get_stats,
    ingest_document,
    ingest_forensic_report,
)
from .ranking import _classify_query
from .rerank import get_reranker, rerank_chunks

__all__ = [
    # Coding-knowledge collections
    "CODING_COLLECTIONS",
    # External re-exports
    "COLLECTIONS",
    "KNOWN_SCAM_PATTERNS",
    # Scam/exploit knowledge collections
    "SCAM_COLLECTIONS",
    # Shared state
    "_classify_query",
    "_embed_by_collection",
    "_enrich_ann_results",
    "_get_redis",
    "_pattern_cache",
    "_pattern_cache_loaded",
    "_preembed_scam_patterns",
    "_seeded",
    "bias_toward_code_chunks",
    "detect_coding_query",
    "detect_scam_patterns",
    "detect_scam_query",
    "extract_code_blocks",
    "format_code_answer",
    "get_goplus_analysis",
    "get_reranker",
    "get_stats",
    "ingest_document",
    "ingest_forensic_report",
    "rerank_chunks",
    "resolve_search_collections",
    "search_coding_knowledge",
    "search_multi_collection",
    "search_scam_knowledge",
    "search_similar",
    "search_with_transform",
    "seed_known_scams",
    "three_pillar_search",
]
