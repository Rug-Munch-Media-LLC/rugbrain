"""
RAG Search Engine package — dense, sparse, entity, hybrid search.

Split from the former ``search/engine.py`` monolith into focused submodules
(≤200 lines each) with identical behavior. This ``__init__`` re-exports the
exact public API the old module exposed so ``rugbrain.search.engine`` and
``from rugbrain.search.engine import X`` keep working unchanged.

Submodules:
  * ``constants``        — collections, query hints, regexes, env defaults
  * ``query_intent``     — scam/coding detection + collection routing
  * ``code_chunks``      — code-carrying chunk helpers
  * ``vector_lane``      — dense vector search + ANN enrichment
  * ``keyword_lane``     — BM25 keyword lane + RRF fusion
  * ``query_expansion``  — HyDE / multi-query / synonym variants
  * ``multi_collection`` — multi-collection orchestration
  * ``pillars``          — three-pillar retrieval lanes + fusion
  * ``postprocess``      — MMR, rerank, parent-child, ratings, confidence
  * ``three_pillar``     — three-pillar orchestrator
  * ``knowledge``        — scam/coding knowledge search
  * ``transform``        — query-transformation search
"""

from rugbrain.crypto_embeddings import COLLECTIONS, get_embedder

from .._state import _get_redis
from .code_chunks import (
    bias_toward_code_chunks,
    extract_code_blocks,
    format_code_answer,
)
from .constants import (
    CODING_COLLECTIONS,
    SCAM_COLLECTIONS,
)
from .keyword_lane import _hybrid_fuse, _hybrid_keyword_search
from .knowledge import search_coding_knowledge, search_scam_knowledge
from .multi_collection import search_multi_collection
from .pillars import _fetch_parent_context
from .query_intent import (
    detect_coding_query,
    detect_scam_query,
    resolve_search_collections,
)
from .three_pillar import three_pillar_search
from .transform import search_with_transform
from .vector_lane import _enrich_ann_results, search_similar

__all__ = [
    "CODING_COLLECTIONS",
    "COLLECTIONS",
    "SCAM_COLLECTIONS",
    "_enrich_ann_results",
    "_fetch_parent_context",
    "_get_redis",
    "_hybrid_fuse",
    "_hybrid_keyword_search",
    "bias_toward_code_chunks",
    "detect_coding_query",
    "detect_scam_query",
    "extract_code_blocks",
    "format_code_answer",
    "get_embedder",
    "resolve_search_collections",
    "search_coding_knowledge",
    "search_multi_collection",
    "search_scam_knowledge",
    "search_similar",
    "search_with_transform",
    "three_pillar_search",
]
