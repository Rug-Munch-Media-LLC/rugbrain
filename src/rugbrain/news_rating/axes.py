"""Axis registry, weights, and shared numeric helpers for news rating."""

from __future__ import annotations

# ── Axis registry ───────────────────────────────────────────────────────
AXES: tuple[str, ...] = (
    "source_credibility",
    "factual_density",
    "timeliness",
    "bias",
    "corroboration",
    "technical_depth",
    "source_diversity",
)

AXIS_WEIGHTS: dict[str, float] = {
    "source_credibility": 0.25,
    "factual_density": 0.15,
    "timeliness": 0.10,
    "bias": 0.15,
    "corroboration": 0.15,
    "technical_depth": 0.10,
    "source_diversity": 0.10,
}

# Legacy factuality/source-reputation thresholds (bridge to ratings.py).
FACTUALITY_VERIFIED_MIN = 75
FACTUALITY_REPORTED_MIN = 50
REPUTATION_ESTABLISHED_MIN = 75
REPUTATION_INDEPENDENT_MIN = 50


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))
