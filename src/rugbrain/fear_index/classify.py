"""Fear-index band classification."""

from __future__ import annotations


def classify_fear(value: int) -> str:
    """Label a 0-100 index: extreme_fear|fear|neutral|greed|extreme_greed."""
    if value < 25:
        return "extreme_fear"
    if value < 40:
        return "fear"
    if value <= 60:
        return "neutral"
    if value <= 74:
        return "greed"
    return "extreme_greed"
