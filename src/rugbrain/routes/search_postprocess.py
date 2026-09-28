"""Post-retrieval enrichment for search results.

One job per function, all fail-open — a failure in enrichment must never break
a search that already returned results:

- :func:`apply_ratings`   — filter by factuality/tier/corroboration, reweight
- :func:`apply_chain_data` — merge chain-indexer parquet rows when asked
- :func:`reflect`          — pre-generation reflection over the final results
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RatingOutcome:
    """What :func:`apply_ratings` did, for the response's diagnostic flags."""

    results: list[dict[str, Any]]
    filters_applied: bool
    boosts_applied: bool


def apply_ratings(
    results: list[dict[str, Any]],
    *,
    factuality_filter: str | None,
    min_source_tier: str | None,
    require_corroboration: bool,
    reranker_applied: bool,
) -> RatingOutcome:
    """Drop rating-conflicting docs, reweight survivors, surface quality fields."""
    filters_applied = any([factuality_filter, min_source_tier, require_corroboration])
    if not filters_applied:
        return RatingOutcome(results, False, False)
    try:
        from rugbrain.ratings import apply_rating_boosts, filter_docs_by_ratings
    except ImportError:
        logger.debug("rugbrain.ratings not available — skipping rating filters")
        return RatingOutcome(results, False, False)
    try:
        out = filter_docs_by_ratings(
            results,
            factuality_filter=factuality_filter,
            min_source_tier=min_source_tier,
            require_corroboration=require_corroboration,
        )
        out = apply_rating_boosts(out)
        boosts_applied = any(r.get("rating_boosted") for r in out)
        if boosts_applied:
            # Re-sort on the boosted score field, preserving rerank ordering
            # when reranking ran, otherwise the retrieval score.
            score_field = "combined_score" if reranker_applied else "similarity"
            out.sort(key=lambda r: r.get(score_field, 0.0), reverse=True)
        _surface_quality_fields(out)
        return RatingOutcome(out, True, boosts_applied)
    except Exception as exc:
        logger.debug("rating filter/boost failed (non-critical): %s", exc)
        return RatingOutcome(results, False, False)


def _surface_quality_fields(results: list[dict[str, Any]]) -> None:
    """Hoist rating metadata onto the result top level so callers can rank."""
    for doc in results:
        meta = doc.get("metadata")
        if not (isinstance(meta, dict) and isinstance(meta.get("rating"), dict)):
            continue
        rating = meta["rating"]
        if isinstance(rating.get("quality_score"), int | float):
            doc["quality_score"] = int(rating["quality_score"])
        for key in ("cross_referenced", "clickbait_score", "on_chain_verified"):
            if key in rating:
                doc[key] = rating[key]


async def reflect(query: str, results: list[dict[str, Any]]) -> Any:
    """Pre-generation reflection over the final results. Fail-open to ``None``."""
    try:
        from rugbrain.pre_generation_reflection import pre_generation_reflection

        return await pre_generation_reflection(query, results)
    except Exception as exc:
        logger.debug("reflection_failed (non-critical): %s", exc)
        return None
