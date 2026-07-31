"""Confidence Scoring Module — composite 0-100 risk/confidence score.

Self-contained port of rmi-backend ``app.confidence`` for the rmi-rag
standalone container. Combines multiple signals into a single
interpretable score with human-readable breakdown.

Signals weighted:
  - Retrieval concentration: how concentrated are top results? (25%)
  - Similarity quality: raw similarity scores (20%)
  - Source corroboration: independent sources agreeing (20%)
  - Reranker margin: gap between top and runner-up (15%)
  - Temporal freshness: how recent is the evidence? (10%)
  - Entity exact-match bonus: direct address/token match (10%)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Weights (sum to 1.0) ──────────────────────────────────────────
WEIGHT_RETRIEVAL_CONCENTRATION = 0.25
WEIGHT_SIMILARITY_QUALITY = 0.20
WEIGHT_SOURCE_CORROBORATION = 0.20
WEIGHT_RERANKER_MARGIN = 0.15
WEIGHT_TEMPORAL_FRESHNESS = 0.10
WEIGHT_ENTITY_BONUS = 0.10


def score_confidence(
    results: list[dict[str, Any]],
    query: str = "",
    entity_matches: list[str] | None = None,
) -> dict[str, Any]:
    """
    Compute composite confidence score from RAG search results.

    Parameters
    ----------
    results: List of result dicts from three_pillar_search, each containing:
        - similarity (float): vector similarity 0-1
        - combined_score (float, optional): post-rerank combined score
        - source (str, optional): source identifier
        - metadata (dict, optional): may contain 'created_at', 'severity'
    query: The original query string (for context)
    entity_matches: List of entity strings that had exact matches

    Returns
    -------
    Dict with:
        - score: int 0-100
        - label: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INCONCLUSIVE"
        - breakdown: Dict of component scores
        - summary: Human-readable one-liner
        - details: List of specific signal descriptions
    """
    if not results:
        return {
            "score": 0,
            "label": "INCONCLUSIVE",
            "breakdown": {},
            "summary": "No evidence found - insufficient data for confidence assessment.",
            "details": [],
        }

    components = {}
    details = []

    # 1. Retrieval Concentration
    concentration = _score_concentration(results)
    components["retrieval_concentration"] = round(concentration, 2)
    if concentration > 0.8:
        details.append("Strong signal: top results highly concentrated")
    elif concentration < 0.3:
        details.append("Weak signal: results are scattered - low confidence pattern")

    # 2. Similarity Quality
    sim_quality = _score_similarity(results)
    components["similarity_quality"] = round(sim_quality, 2)
    if sim_quality > 0.8:
        details.append("High semantic similarity to known patterns")
    elif sim_quality < 0.5:
        details.append("Low similarity - no strong matches found")

    # 3. Source Corroboration
    corroboration = _score_corroboration(results)
    components["source_corroboration"] = round(corroboration, 2)
    unique_sources = len({r.get("source", "") for r in results if r.get("source")})
    if unique_sources >= 3:
        details.append(f"Corroborated by {unique_sources} independent sources")
    elif unique_sources == 0:
        details.append("No source metadata available - cannot verify independence")

    # 4. Reranker Margin
    margin = _score_reranker_margin(results)
    components["reranker_margin"] = round(margin, 2)
    if margin > 0.8:
        details.append("Clear leader: top result significantly stronger than alternatives")
    elif margin < 0.3 and len(results) > 1:
        details.append("Close race - multiple results nearly equal")

    # 5. Temporal Freshness
    freshness = _score_freshness(results)
    components["temporal_freshness"] = round(freshness, 2)
    if freshness > 0.8:
        details.append("Evidence is recent (< 7 days)")
    elif freshness < 0.3:
        details.append("Evidence is old (> 90 days) - may be stale")

    # 6. Entity Exact-Match Bonus
    entity_bonus = _score_entity_match(entity_matches, query)
    components["entity_bonus"] = round(entity_bonus, 2)
    if entity_bonus > 0.5:
        details.append(f"Exact address/token match found: {', '.join(entity_matches[:3])}")

    # ── Composite Score ──────────────────────────────────────────
    composite = (
        WEIGHT_RETRIEVAL_CONCENTRATION * concentration
        + WEIGHT_SIMILARITY_QUALITY * sim_quality
        + WEIGHT_SOURCE_CORROBORATION * corroboration
        + WEIGHT_RERANKER_MARGIN * margin
        + WEIGHT_TEMPORAL_FRESHNESS * freshness
        + WEIGHT_ENTITY_BONUS * entity_bonus
    )

    score = min(100, max(0, round(composite * 100)))

    # ── Label ─────────────────────────────────────────────────────
    if score >= 90:
        label = "CRITICAL"
        summary = f"Extremely high confidence ({score}%) - multiple strong corroborating signals."
    elif score >= 75:
        label = "HIGH"
        summary = f"High confidence ({score}%) - strong evidence from multiple signals."
    elif score >= 55:
        label = "MEDIUM"
        summary = f"Moderate confidence ({score}%) - some signals present but not definitive."
    elif score >= 30:
        label = "LOW"
        summary = f"Low confidence ({score}%) - weak or conflicting signals."
    else:
        label = "INCONCLUSIVE"
        summary = f"Insufficient evidence ({score}%) - need more data for assessment."

    # Add signal count to summary
    signal_count = sum(1 for v in components.values() if v > 0.3)
    if signal_count > 0:
        summary += f" Based on {signal_count} signal categories."

    return {
        "score": score,
        "label": label,
        "breakdown": components,
        "summary": summary,
        "details": details,
    }


# ── Component Scorers ──────────────────────────────────────────────


def _score_concentration(results: list[dict]) -> float:
    """How concentrated are the top similarities? Sharp drop = strong signal."""
    sims = [r.get("similarity", 0) or r.get("combined_score", 0) for r in results[:5]]
    sims = [s for s in sims if s is not None and s > 0]
    if not sims:
        return 0.0
    if len(sims) == 1:
        return 0.5  # single result, neutral

    top = sims[0]
    avg_rest = sum(sims[1:]) / len(sims[1:]) if len(sims) > 1 else 0
    if top == 0:
        return 0.0

    # Ratio of top to average-of-rest. High ratio = high concentration.
    ratio = min(1.0, (top - avg_rest) / top) if top > avg_rest else 0.0
    return ratio


def _score_similarity(results: list[dict]) -> float:
    """Raw semantic similarity quality - how close are top results?"""
    sims = [r.get("similarity", 0) or r.get("combined_score", 0) for r in results[:3]]
    sims = [s for s in sims if s is not None and s > 0]
    if not sims:
        return 0.0

    # Average of top-3 similarities, capped at 0.95 (cosine can be near 1.0 for duplicates)
    avg = sum(sims) / len(sims)
    return min(1.0, avg / 0.9)  # normalize: 0.9+ similarity = 1.0 score


def _score_corroboration(results: list[dict]) -> float:
    """How many independent sources agree?"""
    sources = [r.get("source", "") for r in results if r.get("source")]
    unique = len(set(sources))
    if unique >= 5:
        return 1.0
    elif unique >= 3:
        return 0.8
    elif unique >= 2:
        return 0.5
    elif unique == 1:
        return 0.2
    return 0.0


def _score_reranker_margin(results: list[dict]) -> float:
    """Gap between #1 and #2 combined score. Big gap = clear winner."""
    scores = [r.get("combined_score", r.get("similarity", 0)) for r in results[:3]]
    scores = [s for s in scores if s is not None and s > 0]
    if len(scores) < 2:
        return 0.5  # single result, neutral
    margin = scores[0] - scores[1]
    # Normalize: margin of 0.2+ = 1.0 score
    return min(1.0, margin / 0.2)


def _score_freshness(results: list[dict]) -> float:
    """How recent is the evidence?"""
    now = datetime.now(UTC)
    ages_days = []

    for r in results:
        created = r.get("metadata", {}).get("created_at") or r.get("created_at")
        if created:
            try:
                dt = (
                    datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if isinstance(created, str)
                    else created
                )
                age = (now - dt).days if hasattr(dt, "days") else 365
                ages_days.append(age)
            except (ValueError, TypeError):
                pass

    if not ages_days:
        return 0.5  # no freshness data, neutral

    avg_age = sum(ages_days) / len(ages_days)
    # Score decays: < 1 day = 1.0, 7 days = 0.8, 30 days = 0.5, 90 days = 0.3, > 365 = 0.1
    if avg_age <= 1:
        return 1.0
    elif avg_age <= 7:
        return 0.9
    elif avg_age <= 30:
        return 0.7
    elif avg_age <= 90:
        return 0.4
    elif avg_age <= 180:
        return 0.2
    return 0.1


def _score_entity_match(entity_matches: list[str] | None, query: str) -> float:
    """Bonus for exact entity matches (address, token symbol, hash)."""
    if not entity_matches:
        # Check query for address patterns as a fallback
        eth_match = re.search(r"0x[a-fA-F0-9]{40}", query)
        sol_match = re.search(r"[1-9A-HJ-NP-Za-km-z]{32,44}", query)
        if eth_match or sol_match:
            return 0.5  # query contains an address - strong signal
        return 0.0

    # Score based on match count
    count = len(entity_matches)
    if count >= 3:
        return 1.0
    elif count >= 2:
        return 0.8
    return 0.6
