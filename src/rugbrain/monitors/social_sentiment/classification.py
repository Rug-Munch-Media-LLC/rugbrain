"""Sentiment classification helpers (0-100 score to label)."""

from __future__ import annotations


def classify_sentiment(sentiment: float) -> str:
    """Classify a sentiment score into a label.

    Positive > 60, neutral 40-60, negative < 40.
    """
    if sentiment > 60:
        return "positive"
    elif sentiment < 40:
        return "negative"
    return "neutral"


def get_sentiment_label(sentiment: float) -> str:
    """Get a human-readable label for sentiment extremes."""
    from rugbrain.monitors import social_sentiment as _pkg

    if sentiment >= _pkg.SENTIMENT_BULLISH_THRESHOLD:
        return "🔥 Bullish"
    elif sentiment <= _pkg.SENTIMENT_BEARISH_THRESHOLD:
        return "💀 Bearish"
    return _pkg.classify_sentiment(sentiment).capitalize()
