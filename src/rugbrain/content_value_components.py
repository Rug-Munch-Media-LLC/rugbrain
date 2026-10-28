"""Pure component functions behind a candidate's value score.

Every function here answers ONE question, returns 0..1, and is monotone in the
direction its name implies, so a score can be explained without re-reading the
weighting: the breakdown stored with the score quotes exactly these numbers.

Why these seven (and no model): each is a fact about the CANDIDATE, not a
learned opinion. A learned ranker would need labels we do not have, and an
unexplainable score cannot be argued with in an incident review.

Units, stated so a reader can check the arithmetic:

    novelty            1 - strongest similarity to anything already published
    source_diversity   log2(distinct sources) / 2  → 1 source 0.00, 4 → 1.00
    onchain_magnitude  log10(1 + USD) / 10         → $1M 0.60, $1B 0.90
    recency            0.5 ** (age_hours / 12)     → 12h 0.50, 24h 0.25
    citation_strength  0.4*has_url + 0.4*sources + 0.2*numeric_claims
    reader_interest    category prior + severity bonus
    dedupe_penalty     strongest layer evidence over its own threshold

The on-chain scale is deliberately coarse: a $1M and a $2M drain are the same
editorial event, and a scale that separates them invites false precision.
"""

from __future__ import annotations

import math
from typing import Any, Final

#: Recency half-life. Twelve hours is one news cycle for this audience: a story
#: is half as interesting after a day, and effectively stale by two.
RECENCY_HALF_LIFE_H: Final = 12.0

#: log10(1+USD) divisor; see the module table above.
ONCHAIN_LOG_SCALE: Final = 10.0
#: On-chain credit when we only know "a whale moved" and no USD figure.
WHALE_ONLY_MAGNITUDE: Final = 0.35

#: Reader-interest prior per editorial category. Scam/security content IS the
#: mission (retail protection), so it leads; market recap is the tail.
CATEGORY_PRIOR: Final[dict[str, float]] = {
    "scam": 0.90,
    "security": 0.90,
    "exploit": 0.90,
    "hack": 0.90,
    "phishing": 0.88,
    "vulnerability": 0.85,
    "onchain": 0.75,
    "news": 0.70,
    "research": 0.65,
    "analysis": 0.65,
    "market": 0.55,
    "social": 0.50,
    "x_social": 0.45,
    "recap": 0.40,
}
DEFAULT_PRIOR: Final = 0.50
SEVERITY_BONUS: Final[dict[str, float]] = {
    "critical": 0.10,
    "high": 0.05,
    "medium": 0.0,
    "low": 0.0,
}

#: Citation sub-weights; they sum to 1.
URL_CREDIT: Final = 0.40
SOURCE_CREDIT: Final = 0.40
NUMERIC_CREDIT: Final = 0.20


def novelty(max_similarity: float) -> float:
    """How unlike anything already published this is (1.0 = entirely new)."""
    return round(min(1.0, max(0.0, 1.0 - max(0.0, float(max_similarity)))), 6)


def source_diversity(distinct_sources: int) -> float:
    """Corroboration across independent channels, saturating at four."""
    count = max(0, int(distinct_sources))
    if count <= 1:
        return 0.0
    return round(min(1.0, math.log2(count) / 2.0), 6)


def onchain_magnitude(metadata: dict[str, Any] | None) -> float:
    """Size of the money involved: liquidity, volume, PnL, or whale flag."""
    md = metadata or {}
    usd = 0.0
    for key in ("pnl_usd", "volume_usd", "liquidity_usd", "amount_usd"):
        value = md.get(key)
        if isinstance(value, (int, float)):
            usd = max(usd, abs(float(value)))
    if usd > 0:
        return round(min(1.0, math.log10(1.0 + usd) / ONCHAIN_LOG_SCALE), 6)
    return WHALE_ONLY_MAGNITUDE if md.get("whale") else 0.0


def recency(age_hours: float | None, *, half_life_h: float = RECENCY_HALF_LIFE_H) -> float:
    """Exponential decay from publication. Undated is 0.0, never assumed fresh."""
    if age_hours is None or not math.isfinite(age_hours) or age_hours < 0:
        return 0.0 if age_hours is None else 1.0
    decay: float = 0.5 ** (float(age_hours) / half_life_h)
    return round(decay, 6)


def citation_strength(*, has_url: bool, distinct_sources: int, numeric_claims: int) -> float:
    """Can a reader check this? A link, independent corroboration, real numbers."""
    sources = min(1.0, max(0, int(distinct_sources) - 1) / 3.0)
    numbers = min(1.0, max(0, int(numeric_claims)) / 2.0)
    return round(
        (URL_CREDIT if has_url else 0.0) + SOURCE_CREDIT * sources + NUMERIC_CREDIT * numbers, 6
    )


def reader_interest_prior(category: str, severity: str) -> float:
    """What this beat usually does with this audience, plus a severity nudge."""
    cat = (category or "").strip().lower()
    prior = CATEGORY_PRIOR.get(cat, DEFAULT_PRIOR)
    bonus = SEVERITY_BONUS.get((severity or "").strip().lower(), 0.0)
    return round(min(1.0, prior + bonus), 6)


def dedupe_penalty(
    *,
    exact: bool,
    jaccard: float,
    cosine_value: float | None,
    near_threshold: float,
    semantic_threshold: float,
) -> float:
    """How much of a duplicate this is, on the same 0..1 scale.

    Reported as the strongest layer's evidence NORMALISED by that layer's own
    threshold, so 1.0 means "at or past the bar" whatever the layer. An exact
    hash match is 1.0 by construction: it cannot be anything else.
    """
    if exact:
        return 1.0
    near = min(1.0, max(0.0, float(jaccard)) / near_threshold) if near_threshold else 0.0
    semantic = (
        min(1.0, max(0.0, float(cosine_value)) / semantic_threshold)
        if cosine_value is not None and semantic_threshold
        else 0.0
    )
    return round(max(near, semantic), 6)


__all__ = [
    "CATEGORY_PRIOR",
    "DEFAULT_PRIOR",
    "RECENCY_HALF_LIFE_H",
    "SEVERITY_BONUS",
    "citation_strength",
    "dedupe_penalty",
    "novelty",
    "onchain_magnitude",
    "reader_interest_prior",
    "recency",
    "source_diversity",
]
