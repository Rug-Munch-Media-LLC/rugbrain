"""Parse LunarCrush sentiment payloads into a 0-100 score."""

from __future__ import annotations

from typing import Any


def _parse_sentiment_score(raw: Any) -> float:
    """Parse LunarCrush sentiment into a 0-100 score.

    LunarCrush can return:
    - Numeric score (e.g., 75.5)
    - String label ("positive", "negative", "neutral")
    - None / missing
    """
    if raw is None:
        return 50.0
    if isinstance(raw, (int, float)):
        return max(0.0, min(100.0, float(raw)))
    if isinstance(raw, str):
        mapping = {
            "positive": 80.0,
            "very positive": 90.0,
            "extremely positive": 95.0,
            "neutral": 50.0,
            "negative": 20.0,
            "very negative": 10.0,
            "extremely negative": 5.0,
        }
        return mapping.get(raw.lower().strip(), 50.0)
    return 50.0
