"""Rapid sentiment reversal detection against a rolling average."""

from __future__ import annotations

from typing import Any


def detect_sentiment_reversal(symbol: str, current_sentiment: float) -> dict[str, Any] | None:
    """Detect rapid sentiment reversal compared to rolling average.

    Returns reversal info if |current - avg| > SENTIMENT_REVERSAL_THRESHOLD.
    """
    from rugbrain.monitors import social_sentiment as _pkg

    history = _pkg._sentiment_history.get(symbol)
    if not history or len(history) < 3:
        return None

    sentiments = [h["sentiment"] for h in history]
    avg_sentiment = sum(sentiments) / len(sentiments)
    change = current_sentiment - avg_sentiment

    if abs(change) >= _pkg.SENTIMENT_REVERSAL_THRESHOLD:
        direction = "positive" if change > 0 else "negative"
        return {
            "symbol": symbol,
            "previous_avg_sentiment": round(avg_sentiment, 1),
            "current_sentiment": current_sentiment,
            "change": round(change, 1),
            "direction": direction,
            "severity": (
                "high" if abs(change) >= _pkg.SENTIMENT_REVERSAL_THRESHOLD * 1.5 else "medium"
            ),
        }
    return None
