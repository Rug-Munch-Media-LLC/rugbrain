"""Explainable value score per content candidate.

One number decides where an item goes, so that number has to be arguable. The
score is a weighted sum of six measured components MINUS a dedupe penalty, and
the components — raw value, weight, contribution — are stored WITH the score
(``value_breakdown``). A reviewer can read why an item routed where it did
without re-running the pipeline.

Weights sum to 1.0 over the positive components, so a perfect candidate scores
1.0; :data:`DEDUPE_WEIGHT` is subtracted separately, which is why a duplicate
scores near zero rather than merely low. Editorial weighting rationale:

    novelty .22        the channel's value is "you have not seen this"
    dedupe penalty .35 the single strongest signal that we must not post again
    onchain magnitude .18  money moved is the beat's headline fact
    reader interest .18    scam/security content leads the audience prior
    recency .16        a day-old story is worth half as much
    source diversity .14   corroboration is evidence, not a leaderboard
    citation strength .12  link + sources + numbers let a reader check us

Novelty and the dedupe penalty are deliberately separate: novelty describes the
candidate against the published pool, the penalty describes how much of it is a
duplicate OF A SPECIFIC story. A story that is merely similar to everything
scores low; a story that IS something we posted scores zero.

No I/O, no clock unless injected: ``now`` is a parameter so a score is
reproducible in a test and a re-run is byte-identical.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from rugbrain import content_value_components as parts
from rugbrain.content_dedupe_layers import NEAR_THRESHOLD, SEMANTIC_THRESHOLD

#: Positive component weights. Sum == 1.0 (asserted in the test suite).
WEIGHTS: Final[dict[str, float]] = {
    "novelty": 0.22,
    "source_diversity": 0.14,
    "onchain_magnitude": 0.18,
    "recency": 0.16,
    "citation_strength": 0.12,
    "reader_interest_prior": 0.18,
}

#: Dedupe penalty weight, subtracted after the weighted sum.
DEDUPE_WEIGHT: Final = 0.35

#: Values are rounded to this many decimals so a stored breakdown is stable.
DP: Final = 4


def _age_hours(story: dict[str, Any], now: datetime) -> float | None:
    """Age in hours from ``ts``; None when the timestamp will not parse."""
    raw = story.get("ts")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (now - parsed).total_seconds() / 3600.0


def _numeric_claims(text: str) -> int:
    """Count distinct money/percentage figures — a checkable-claim proxy."""
    found = 0
    for token in str(text).split():
        cleaned = token.strip(".,;:()[]<>\"'").replace(",", "").lstrip("$")
        if cleaned.endswith("%"):
            cleaned = cleaned[:-1]
        if cleaned[:1].isdigit() and any(ch.isdigit() for ch in cleaned):
            found += 1
    return min(4, found) // 2


def score_candidate(
    story: dict[str, Any],
    *,
    now: datetime | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score one candidate and return ``{"value": float, "value_breakdown": {...}}``.

    Args:
        story: normalised story dict (``title``, ``content``, ``ts``,
            ``category``, ``severity``, ``url``, ``metadata``, optional
            ``also_seen_in``).
        now: reference time for recency (default: current UTC — inject it in a
            test so the score is reproducible).
        evidence: output of :func:`rugbrain.content_dedupe_layers.layers_hit`
            against the strongest already-published match. None means "nothing
            comparable", which scores as fully novel.

    Returns:
        The value in 0..1 and the full component breakdown, each entry carrying
        ``raw``, ``weight`` and ``contribution`` so the arithmetic is auditable.
    """
    moment = now or datetime.now(UTC)
    proof = evidence or {"exact": False, "jaccard": 0.0, "cosine": None, "similarity": 0.0}
    sources = 1 + len(story.get("also_seen_in") or [])
    metadata = story.get("metadata") or {}

    raw = {
        "novelty": parts.novelty(float(proof.get("similarity") or 0.0)),
        "source_diversity": parts.source_diversity(sources),
        "onchain_magnitude": parts.onchain_magnitude(metadata),
        "recency": parts.recency(_age_hours(story, moment)),
        "citation_strength": parts.citation_strength(
            has_url=bool(story.get("url")),
            distinct_sources=sources,
            numeric_claims=_numeric_claims(str(story.get("content") or "")),
        ),
        "reader_interest_prior": parts.reader_interest_prior(
            str(story.get("category") or ""), str(story.get("severity") or "")
        ),
    }
    penalty = parts.dedupe_penalty(
        exact=bool(proof.get("exact")),
        jaccard=float(proof.get("jaccard") or 0.0),
        cosine_value=proof.get("cosine"),
        near_threshold=NEAR_THRESHOLD,
        semantic_threshold=SEMANTIC_THRESHOLD,
    )

    components = {
        name: {
            "raw": raw[name],
            "weight": weight,
            "contribution": round(raw[name] * weight, DP),
        }
        for name, weight in WEIGHTS.items()
    }
    positive = round(sum(item["contribution"] for item in components.values()), DP)
    negative = round(penalty * DEDUPE_WEIGHT, DP)
    value = round(min(1.0, max(0.0, positive - negative)), DP)
    return {
        "value": value,
        "value_breakdown": {
            "components": components,
            "dedupe_penalty": {
                "raw": penalty,
                "weight": DEDUPE_WEIGHT,
                "contribution": -negative,
            },
            "positive_sum": positive,
            "value": value,
        },
    }


__all__ = ["DEDUPE_WEIGHT", "WEIGHTS", "score_candidate"]
