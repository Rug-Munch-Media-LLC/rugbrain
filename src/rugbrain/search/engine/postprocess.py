"""
RAG Search Engine — post-fusion result processing.

MMR deduplication, cross-encoder reranking (news-lane + search-lane),
parent-child context expansion, rating boosts and confidence scoring applied
to the fused three-pillar result list.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .code_chunks import bias_toward_code_chunks
from .enrich import (
    _apply_parent_child,
    _apply_rating_boosts,
    _apply_reflection,
    _score_confidence,
)

logger = logging.getLogger(__name__)


async def _apply_mmr(
    fused: list[dict[str, Any]],
    limit: int,
    use_mmr: bool,
    mmr_lambda: float,
) -> tuple[list[dict[str, Any]], bool, int]:
    """MMR deduplication of the fused list. Returns (fused, applied, pre_count)."""
    pre_mmr_count = len(fused)
    mmr_applied = False
    if use_mmr and len(fused) > limit:
        try:
            from rugbrain.compat import mmr_dedup_results  # external dependency

            fused = await mmr_dedup_results(
                fused,
                query_score_field="rrf_score",
                content_field="content",
                lambda_param=mmr_lambda,
                top_k=limit * 3,
            )
            mmr_applied = True
            logger.info(
                "MMR dedup: %s → %s results (lambda=%s)", pre_mmr_count, len(fused), mmr_lambda
            )
        except ImportError:
            logger.debug("MMR dedup module not available, skipping")
        except Exception as e:
            logger.debug("MMR dedup failed (non-critical): %s", e)
    return fused, mmr_applied, pre_mmr_count


async def _apply_news_reranker(
    query: str,
    fused: list[dict[str, Any]],
    limit: int,
    use_reranker: bool,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """News-lane cross-encoder reranking (Qwen3-Reranker-8B), graceful no-op."""
    reranker_applied = False
    reranker_method: str | None = None
    if use_reranker and fused:
        for r in fused:
            r["similarity"] = r.get("rrf_score", 0.0)
        pre_rerank = len(fused)

        # Self-hosted Qwen3-Reranker-8B (llama.cpp:8003) — true cross-encoder.
        # Graceful no-op: falls back to raw retrieval order when the server
        # is unreachable (rerank_method="none", reranked=false).
        try:
            from ..rerank import rerank_chunks

            reranked = await rerank_chunks(
                query,
                fused,
                top_n=pre_rerank,
                keep=limit * 3,
            )
            if reranked and reranked[0].get("rerank_method", "none") != "none":
                fused = reranked
                reranker_applied = True
                reranker_method = reranked[0].get("rerank_method", "qwen3-reranker-8b")
                logger.info(
                    "Cross-encoder (Qwen3-Reranker-8B) reranked: %d → %d results",
                    pre_rerank,
                    len(fused),
                )
            else:
                logger.debug("Reranker backend unavailable — skipping cross-encoder stage")
        except ImportError:
            logger.debug("rugbrain.search.rerank not available")
        except Exception as e:
            logger.debug("Qwen3 cross-encoder rerank failed (non-critical): %s", e)
    return fused, reranker_applied, reranker_method


@dataclass
class PostprocessOptions:
    """Knobs for the post-fusion pipeline (keeps function arity ≤5)."""

    use_mmr: bool
    mmr_lambda: float
    use_reranker: bool
    use_parent_child: bool
    use_rating_boosts: bool
    rerank: bool
    rerank_top_k: int
    rerank_keep: int | None
    rerank_timeout: float
    entity_doc_ids: set[str]


async def _apply_search_reranker(
    query: str,
    fused: list[dict[str, Any]],
    limit: int,
    options: PostprocessOptions,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Search-lane cross-encoder reranking (default ON, before truncation)."""
    search_reranker_applied = False
    search_reranker_method: str | None = None
    if options.rerank and fused:
        from ..rerank import rerank_chunks

        pre_rerank = len(fused)
        search_reranked: list[dict[str, Any]] | None = None
        try:
            search_reranked = await asyncio.wait_for(
                rerank_chunks(
                    query,
                    fused,
                    top_n=options.rerank_top_k,
                    keep=options.rerank_keep if options.rerank_keep is not None else limit,
                    tier="live",
                ),
                timeout=None if options.rerank_timeout <= 0 else options.rerank_timeout,
            )
        except Exception as e:
            # Reranker unavailable/slow — keep raw ranking, never fail.
            logger.warning(
                "search-lane rerank timed out/failed after %.1fs (%s) — keeping raw ranking",
                options.rerank_timeout,
                e,
            )
            search_reranked = None
        if search_reranked:
            fused = search_reranked
            search_reranker_applied = any(r.get("reranked") for r in search_reranked)
            methods = {r.get("rerank_method") for r in search_reranked if r.get("rerank_method")}
            search_reranker_method = next(iter(methods), None)
            logger.info(
                "Search-lane reranker: %d → %d results (method=%s)",
                pre_rerank,
                len(fused),
                search_reranker_method,
            )
    return fused, search_reranker_applied, search_reranker_method


async def postprocess_results(
    query: str,
    fused: list[dict[str, Any]],
    limit: int,
    options: PostprocessOptions,
) -> dict[str, Any]:
    """Run every post-fusion stage and return the final result payload."""
    fused, mmr_applied, pre_mmr_count = await _apply_mmr(fused, limit, options.use_mmr, options.mmr_lambda)
    fused, reranker_applied, reranker_method = await _apply_news_reranker(
        query, fused, limit, options.use_reranker
    )
    fused, parent_child_applied = await _apply_parent_child(fused, limit, options.use_parent_child)
    fused, search_reranker_applied, search_reranker_method = await _apply_search_reranker(
        query, fused, limit, options
    )

    final_results = bias_toward_code_chunks(fused, query)[:limit]
    final_results, rating_boosts_applied = _apply_rating_boosts(final_results, options.use_rating_boosts)
    # Code-intent queries keep code chunks on top even when rating boosts ran.
    final_results = bias_toward_code_chunks(final_results, query)

    confidence = _score_confidence(query, final_results, options.entity_doc_ids)
    reflection = await _apply_reflection(query, final_results)

    return {
        "results": final_results,
        "reflection": reflection,
        "mmr_applied": mmr_applied,
        "pre_mmr_count": pre_mmr_count,
        "post_mmr_count": len(fused) if mmr_applied else pre_mmr_count,
        "reranker_applied": reranker_applied,
        "reranker_method": reranker_method,
        "parent_child_applied": parent_child_applied,
        "rating_boosts_applied": rating_boosts_applied,
        "search_reranker_applied": search_reranker_applied,
        "search_reranker_method": search_reranker_method,
        "confidence": confidence,
    }
