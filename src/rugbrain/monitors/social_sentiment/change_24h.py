"""24h change calculations over the rolling sentiment/volume windows."""

from __future__ import annotations

from typing import Any


def calculate_24h_change(symbol: str) -> dict[str, Any]:
    """Calculate 24h change in sentiment and volume for a symbol."""
    from rugbrain.monitors import social_sentiment as _pkg

    sent_history = list(_pkg._sentiment_history.get(symbol, []))
    vol_history = list(_pkg._volume_history.get(symbol, []))

    result: dict[str, Any] = {
        "sentiment_change_24h": 0.0,
        "volume_change_24h": 0.0,
        "sentiment_high_24h": 0.0,
        "sentiment_low_24h": 100.0,
        "volume_total_24h": 0,
    }

    if sent_history:
        current = sent_history[-1]["sentiment"] if sent_history else 50.0
        baseline = sent_history[0]["sentiment"] if sent_history else 50.0
        result["sentiment_change_24h"] = round(current - baseline, 1)
        result["sentiment_high_24h"] = max(h["sentiment"] for h in sent_history)
        result["sentiment_low_24h"] = min(h["sentiment"] for h in sent_history)

    if vol_history:
        result["volume_total_24h"] = sum(h["volume"] for h in vol_history)
        if len(vol_history) > 1:
            mid = len(vol_history) // 2
            first_half = sum(h["volume"] for h in vol_history[:mid])
            second_half = sum(h["volume"] for h in vol_history[mid:])
            if first_half > 0:
                result["volume_change_24h"] = round(
                    ((second_half - first_half) / first_half) * 100, 1
                )

    return result
