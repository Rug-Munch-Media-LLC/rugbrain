"""
RAG Search Engine — multi-collection search orchestration.

Runs the dense lane across collections in parallel, optionally fuses the
keyword/BM25 lane, applies recency decay and the search-lane reranker, then
biases code chunks for code-intent queries.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from rugbrain.rrf_fusion import _rrf_fuse

from .code_chunks import bias_toward_code_chunks
from .constants import (
    _CODE_CONTENT_LIMIT,
    _HYBRID_KEYWORD_LIMIT_FACTOR,
    _RERANK_DEFAULT,
    _RERANK_TIMEOUT,
    _SYNONYM_EXPAND_DEFAULT,
)
from .keyword_lane import _hybrid_fuse
from .query_expansion import _build_search_variants
from .query_intent import detect_coding_query, resolve_search_collections

logger = logging.getLogger(__name__)


async def search_multi_collection(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    content_limit: int = 500,
    hybrid: bool = False,
    rerank: bool = _RERANK_DEFAULT,
    rerank_top_k: int = 20,
    rerank_keep: int | None = None,
    rerank_timeout: float = _RERANK_TIMEOUT,
    hyde: bool = False,
    multi_query: bool = False,
    synonym_expand: bool = _SYNONYM_EXPAND_DEFAULT,
    llm_fn: Callable[..., Any] | None = None,
    use_recency_decay: bool = False,
    recency_half_life_days: float = 30.0,
    recency_date_field: str = "published",
) -> list[dict[str, Any]]:
    """Search across multiple collections and merge results.

    When *collections* is None, scam/exploit queries are routed to
    SCAM_COLLECTIONS (then the defaults) so exploit knowledge is prioritised
    automatically; see ``resolve_search_collections``.

    Code-intent queries fetch longer content (``content_limit`` is raised so
    fenced code blocks stay intact) and the merged results are re-ranked so
    code-carrying chunks float above prose (see ``bias_toward_code_chunks``).

    Search-quality parameters:

    * ``rerank`` — **default True**: the top ``rerank_top_k`` candidates are
      re-ranked with a cross-encoder (Qwen3-Reranker-4B) and the best
      ``rerank_keep`` (defaults to *limit*) are kept before truncation.
      Degrades gracefully: when the model backend is unavailable (or scoring
      exceeds ``rerank_timeout``), results keep their original retrieval order
      and are tagged ``rerank_method="none"`` / ``reranked=false`` — the
      request never fails. Disable with ``rerank=False`` or env
      ``RAG_RERANK_ENABLED=0``.
    * ``rerank_timeout`` — seconds before the reranker falls back to raw
      ranking (default 2.0; env ``RAG_RERANK_TIMEOUT``; ``<=0`` disables).
    * ``hybrid`` — when True, a lightweight keyword/BM25 lane runs alongside
      the vector search and the two lanes are fused with reciprocal rank
      fusion (RRF), surfacing keyword-only hits the vector search missed.
    * ``hyde`` — when True, a HyDE hypothetical document is embedded and
      searched as an extra retrieval vector (see
      ``rugbrain.search.query_transform.hyde_expand``).
    * ``multi_query`` — when True, the query is rewritten into 3-4 technical
      variations and each is searched; per-collection results are RRF-fused
      (see ``rugbrain.search.query_transform.multi_query_expand``).
    * ``synonym_expand`` — when True, curated crypto-term aliases ("rug
      pull"→"rugpull/honeypot", "flash loan"→"flashloan", "sandwich"→
      "MEV/frontrunning", ...) are added as extra retrieval variants (see
      ``rugbrain.search.query_transform.expand_synonyms``). OFF by default.
    * ``llm_fn`` — optional LLM callable for the expansion above (sync or
      async). Absent/failing ⇒ no-op fallback (expansion still works).
    * ``use_recency_decay`` — when True, a half-life decay on the
      ``published``/date metadata is applied to fused scores so fresher docs
      rank above equally-relevant stale ones (see
      ``rugbrain.search.fusion.apply_recency_decay``).
    * ``recency_half_life_days`` / ``recency_date_field`` — decay half-life
      and metadata date key (defaults 30 days / ``published``).
    """
    collections = resolve_search_collections(collections, query)
    if detect_coding_query(query):
        content_limit = max(content_limit, _CODE_CONTENT_LIMIT)

    # Query expansion (HyDE + multi-query + synonyms) — additive, default OFF.
    if hyde or multi_query or synonym_expand:
        from rugbrain.search import engine as _e

        embedder = await _e.get_embedder()
        variants, _expansion_meta = await _build_search_variants(
            query, hyde, multi_query, embedder, llm_fn, synonym_expand=synonym_expand
        )
        expanded = len(variants) > 1
    else:
        variants = [query]
        expanded = False

    async def _search_one(coll: str) -> list[dict[str, Any]]:
        from rugbrain.search import engine as _e

        try:
            if not expanded:
                results = await _e.search_similar(
                    query,
                    collection=coll,
                    limit=limit,
                    min_similarity=min_similarity,
                    content_limit=content_limit,
                )
                for r in results:
                    if "collection" not in r:
                        r["collection"] = coll
                return results
            # Expanded: search every variant, then RRF-fuse per collection so
            # the best-of across rewrites wins (bounded to limit * 2).
            variant_lists: list[list[dict[str, Any]]] = []
            for variant in variants:
                results = await _e.search_similar(
                    variant,
                    collection=coll,
                    limit=limit,
                    min_similarity=min_similarity,
                    content_limit=content_limit,
                )
                for r in results:
                    if "collection" not in r:
                        r["collection"] = coll
                if results:
                    variant_lists.append(results)
            if not variant_lists:
                return []
            ranked = [
                sorted(vl, key=lambda x: float(x.get("similarity", 0.0) or 0.0), reverse=True)
                for vl in variant_lists
            ]
            return _rrf_fuse(ranked, k=60)[: limit * 2]
        except Exception as e:
            logger.warning("Search failed for %s: %s", coll, e)
            return []

    # Search all collections in parallel
    all_per_collection = await asyncio.gather(*[_search_one(c) for c in collections])

    if hybrid:
        # Keyword/BM25 lane alongside the vector search; RRF-fuse both lanes.
        from rugbrain.search import engine as _e

        keyword_results = await _e._hybrid_keyword_search(
            query, collections, limit=limit * _HYBRID_KEYWORD_LIMIT_FACTOR
        )
        if keyword_results:
            fused = _hybrid_fuse(list(all_per_collection), keyword_results)
        else:
            fused = [r for results in all_per_collection for r in results]
    else:
        fused = [r for results in all_per_collection for r in results]

    if use_recency_decay and fused:
        from ..fusion import apply_recency_decay

        fused = apply_recency_decay(
            fused,
            half_life_days=recency_half_life_days,
            date_field=recency_date_field,
        )

    if rerank and fused:
        from ..rerank import rerank_chunks

        try:
            fused = await asyncio.wait_for(
                rerank_chunks(
                    query,
                    fused,
                    top_n=rerank_top_k,
                    keep=rerank_keep if rerank_keep is not None else limit,
                    tier="live",
                ),
                timeout=None if rerank_timeout <= 0 else rerank_timeout,
            )
        except Exception as e:
            # Reranker unavailable/slow — fall back to raw ranking, never fail.
            logger.warning(
                "search rerank timed out/failed after %.1fs (%s) — falling back to raw ranking",
                rerank_timeout,
                e,
            )
            for r in fused:
                r.setdefault("rerank_method", "none")
                r.setdefault("reranked", False)

    return bias_toward_code_chunks(fused, query)[:limit]
