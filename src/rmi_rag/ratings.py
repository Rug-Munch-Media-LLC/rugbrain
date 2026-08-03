"""
Ground News-style ratings for the RugSense RAG pipeline.

News articles retrieved by the RAG pipeline carry an optional ``rating``
dict inside their ``metadata`` payload. This module centralises:

  - ``apply_rating_boosts`` — quality-based score reweighting after
    retrieval and/or reranking.
  - ``filter_docs_by_ratings`` — post-search filtering by factuality tier,
    minimum source tier and corroboration requirement.
  - ``format_rag_answer_with_ratings`` — answer formatting with source
    badges and low-confidence disclaimers.
  - ``synthesize_answer`` — rating-aware answer node used by Graph RAG.

Expected rating payload shape (any subset is valid)::

    metadata["rating"] = {
        "factuality": "verified" | "reported" | "rumor",
        "source_reputation": "established" | "independent",
        "market_impact": "high" | "medium" | "low",
        "corroborating_sources": int,        # number of independent sources
        "on_chain_evidence": bool,           # claim verifiable on-chain
    }

Docs are plain dicts (the format used across the pipeline), so the boost
mutates the doc's primary score field in place and records the applied
multiplier on the doc for transparency.
"""

from __future__ import annotations

from typing import Any

logger: Any = None  # replaced lazily to keep import side-effect free

# ── Score field priority: reranked docs carry these first ───────────
_SCORE_FIELDS = ("combined_score", "cross_score", "rrf_score", "similarity", "score")

# ── Rating multipliers (Ground News-style quality weights) ───────────
FACTUALITY_MULTIPLIERS = {
    "verified": 1.2,
    "rumor": 0.5,
}
SOURCE_REPUTATION_MULTIPLIERS = {
    "established": 1.1,
}
MARKET_IMPACT_MULTIPLIERS = {
    "high": 1.3,
}
CORROBORATION_MIN = 3
CORROBORATION_HIGH_MULTIPLIER = 1.15
CORROBORATION_NONE_MULTIPLIER = 0.8
ON_CHAIN_EVIDENCE_MULTIPLIER = 1.25

# Valid filter values exposed to the API
FACTUALITY_FILTERS = ("verified", "reported", "rumor")
SOURCE_TIERS = ("established", "independent")


def _rating(doc: dict[str, Any]) -> dict[str, Any]:
    """Extract the rating dict from a doc's metadata (never raises)."""
    meta = doc.get("metadata")
    if isinstance(meta, dict):
        rating = meta.get("rating")
        if isinstance(rating, dict):
            return rating
    # Allow a top-level rating for callers that flatten metadata
    rating = doc.get("rating")
    return rating if isinstance(rating, dict) else {}


def _primary_score(doc: dict[str, Any]) -> float:
    """Return the first present score field, or 0.0."""
    for field in _SCORE_FIELDS:
        value = doc.get(field)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _set_primary_score(doc: dict[str, Any], value: float) -> None:
    """Write a value to the doc's first present score field."""
    for field in _SCORE_FIELDS:
        if isinstance(doc.get(field), (int, float)):
            doc[field] = round(value, 6)
            return
    # No recognised score field — store under the conventional key
    doc["score"] = round(value, 6)


def rating_multiplier(rating: dict[str, Any]) -> float:
    """Compute the composite score multiplier for a rating dict."""
    multiplier = 1.0

    factuality = rating.get("factuality")
    if factuality in FACTUALITY_MULTIPLIERS:
        multiplier *= FACTUALITY_MULTIPLIERS[factuality]

    reputation = rating.get("source_reputation")
    if reputation in SOURCE_REPUTATION_MULTIPLIERS:
        multiplier *= SOURCE_REPUTATION_MULTIPLIERS[reputation]

    impact = rating.get("market_impact")
    if impact in MARKET_IMPACT_MULTIPLIERS:
        multiplier *= MARKET_IMPACT_MULTIPLIERS[impact]

    corroboration = rating.get("corroborating_sources", 0)
    # Only apply the single-source penalty when the rating explicitly reports
    # zero corroboration; a rating dict without the key is neutral.
    if isinstance(corroboration, int) and "corroborating_sources" in rating:
        if corroboration >= CORROBORATION_MIN:
            multiplier *= CORROBORATION_HIGH_MULTIPLIER
        elif corroboration == 0:
            multiplier *= CORROBORATION_NONE_MULTIPLIER

    if rating.get("on_chain_evidence"):
        multiplier *= ON_CHAIN_EVIDENCE_MULTIPLIER

    return multiplier


def apply_rating_boosts(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Boost articles based on their Ground News ratings.

    Mutates each doc's primary score field in place and records the applied
    multiplier under ``rating_multiplier`` (and ``rating_boosted=True``).
    Docs without a rating payload are returned untouched.
    """
    for doc in docs:
        rating = _rating(doc)
        if not rating:
            continue
        multiplier = rating_multiplier(rating)
        if multiplier == 1.0:
            continue
        score = _primary_score(doc)
        if score <= 0:
            continue
        _set_primary_score(doc, score * multiplier)
        doc["rating_multiplier"] = round(multiplier, 4)
        doc["rating_boosted"] = True
    return docs


def passes_rating_filters(
    doc: dict[str, Any],
    *,
    factuality_filter: str | None = None,
    min_source_tier: str | None = None,
    require_corroboration: bool = False,
) -> bool:
    """Return True when a doc satisfies the requested rating filters.

    Filters only exclude docs that *claim* a rating conflicting with the
    request. Docs without a rating payload pass through (ratings are an
    optional enrichment, never a hard gate).
    """
    rating = _rating(doc)
    if not rating:
        return True

    if factuality_filter is not None:
        factuality = rating.get("factuality")
        if factuality is not None and factuality != factuality_filter:
            return False

    if min_source_tier is not None:
        reputation = rating.get("source_reputation")
        if reputation is not None and reputation != min_source_tier:
            return False

    if require_corroboration:
        corroboration = rating.get("corroborating_sources", 0)
        if isinstance(corroboration, int) and corroboration < CORROBORATION_MIN:
            return False

    return True


def filter_docs_by_ratings(
    docs: list[dict[str, Any]],
    *,
    factuality_filter: str | None = None,
    min_source_tier: str | None = None,
    require_corroboration: bool = False,
) -> list[dict[str, Any]]:
    """Filter docs after retrieval using optional rating filters."""
    return [
        doc
        for doc in docs
        if passes_rating_filters(
            doc,
            factuality_filter=factuality_filter,
            min_source_tier=min_source_tier,
            require_corroboration=require_corroboration,
        )
    ]


# ═══════════════════════════════════════════════════════════════════
# Answer formatting
# ═══════════════════════════════════════════════════════════════════


def _source_name(source: dict[str, Any]) -> str:
    for field in ("source", "title", "name", "id"):
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Unknown"


def source_badge(source: dict[str, Any]) -> str:
    """Build a Ground News-style source badge string.

    Example: ``[CoinDesk · Verified · 3 sources corroborate]``
    """
    rating = _rating(source)
    parts = [_source_name(source)]

    factuality = rating.get("factuality")
    if isinstance(factuality, str):
        parts.append(factuality.capitalize())

    corroboration = rating.get("corroborating_sources", 0)
    if isinstance(corroboration, int) and corroboration >= CORROBORATION_MIN:
        parts.append(f"{corroboration} sources corroborate")

    return "[" + " · ".join(parts) + "]"


def format_rag_answer_with_ratings(
    answer: str,
    sources: list[dict[str, Any]],
) -> str:
    """Format answer with Ground News-style source attribution.

    - Adds a ``[Source · Factuality · N sources corroborate]`` badge per source.
    - Adds a disclaimer for low-confidence sources (rumor/unverified,
      unestablished, or single-source claims).
    """
    lines = [answer]

    if sources:
        lines.append("\n**Sources:**")
        for source in sources:
            lines.append(f"- {source_badge(source)}")

    low_confidence = [source for source in sources if _is_low_confidence(source)]
    if low_confidence:
        names = ", ".join(_source_name(s) for s in low_confidence[:3])
        lines.append(
            f"\n*Disclaimer: claims from {names} have lower confidence "
            "(unverified, unestablished, or uncorroborated). Treat with caution.*"
        )

    return "\n".join(lines)


def _is_low_confidence(source: dict[str, Any]) -> bool:
    rating = _rating(source)
    if not rating:
        return False

    if rating.get("factuality") == "rumor":
        return True

    corroboration = rating.get("corroborating_sources", 0)
    if isinstance(corroboration, int) and corroboration == 0:
        return True

    return rating.get("source_reputation") not in ("established", None)


def prioritize_verified_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort sources so verified, corroborated ones appear first.

    Verified first, then reported, then rumor; ties broken by corroboration
    count descending. Sources without ratings keep their relative order.
    """

    def _rank(source: dict[str, Any]) -> tuple[int, int]:
        rating = _rating(source)
        factuality = rating.get("factuality")
        if factuality == "verified":
            f_rank = 0
        elif factuality == "reported":
            f_rank = 1
        elif factuality == "rumor":
            f_rank = 3
        else:
            f_rank = 2
        corroboration = rating.get("corroborating_sources", 0)
        if not isinstance(corroboration, int):
            corroboration = 0
        return (f_rank, -corroboration)

    return sorted(sources, key=_rank)


# ═══════════════════════════════════════════════════════════════════
# Rating-aware answer synthesis (Graph RAG node)
# ═══════════════════════════════════════════════════════════════════


def synthesize_answer(
    answer: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Synthesize an answer that surfaces Ground News ratings.

    Returns a dict with:
      - ``answer``: rating-formatted answer (badges + disclaimers)
      - ``citations``: sources ordered with verified first
      - ``rating_summary``: aggregate corroboration / rumor counts

    Used by the Graph RAG ``synthesize_answer`` node so citations
    prioritise verified sources, rumor-based claims carry a disclaimer,
    and corroboration counts are visible in the output.
    """
    ordered = prioritize_verified_sources(sources)
    formatted = format_rag_answer_with_ratings(answer, ordered)

    ratings = [_rating(s) for s in sources if _rating(s)]
    corroboration_counts = [
        r.get("corroborating_sources", 0)
        for r in ratings
        if isinstance(r.get("corroborating_sources", 0), int)
    ]
    rumor_count = sum(1 for r in ratings if r.get("factuality") == "rumor")
    verified_count = sum(1 for r in ratings if r.get("factuality") == "verified")

    return {
        "answer": formatted,
        "citations": ordered,
        "rating_summary": {
            "source_count": len(sources),
            "rated_count": len(ratings),
            "verified_count": verified_count,
            "rumor_count": rumor_count,
            "max_corroboration": max(corroboration_counts, default=0),
        },
    }
