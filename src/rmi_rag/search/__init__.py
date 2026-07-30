"""
RAG Search package — engine, ranking, filters.

Re-exports all public API from submodules for backward compatibility.
"""

# ── Submodules ──
from ._state import (
    _get_redis,
    _pattern_cache,
    _pattern_cache_loaded,
    _seeded,
)
from .engine import (
    _enrich_ann_results,
    search_multi_collection,
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

# ── External re-exports (backward compat) ──
from rmi_rag.crypto_embeddings import COLLECTIONS, KNOWN_SCAM_PATTERNS
from rmi_rag.scam_detection import detect_scam_patterns  # noqa: F401 — re-exported
from rmi_rag.seed_scams import seed_known_scams  # noqa: F401 — re-exported

__all__ = [
    # Shared state
    "_get_redis",
    "_pattern_cache",
    "_pattern_cache_loaded",
    "_seeded",
    # Ranking
    "_classify_query",
    # Engine
    "_enrich_ann_results",
    "search_similar",
    "search_multi_collection",
    "three_pillar_search",
    "search_with_transform",
    # Filters
    "_embed_by_collection",
    "_preembed_scam_patterns",
    "ingest_document",
    "ingest_forensic_report",
    "get_goplus_analysis",
    "get_stats",
    # External re-exports
    "COLLECTIONS",
    "KNOWN_SCAM_PATTERNS",
    "detect_scam_patterns",
    "seed_known_scams",
]
