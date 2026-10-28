"""Result dataclasses for text, token and market sentiment.

Moved verbatim from ``sentiment_engine.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class SentimentResult:
    """Structured sentiment output from text analysis."""

    sentiment: str  # "bearish", "bullish", "neutral"
    confidence: float  # 0.0-1.0
    signals: list[str]  # matched keywords
    scores: dict[str, float] = field(default_factory=dict)  # bearish/bullish/neutral raw scores
    analyzed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    text_length: int = 0


@dataclass
class TokenSentiment:
    """Aggregated sentiment for a single token."""

    address: str
    chain: str
    sentiment: str
    confidence: float
    signals: list[str]
    source_counts: dict[str, int] = field(default_factory=dict)
    analyzed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class MarketSentiment:
    """Overall crypto market sentiment snapshot."""

    sentiment: str
    confidence: float
    score: float  # -1.0 (extreme fear) to +1.0 (extreme greed)
    sources: int
    top_signals: list[str]
    components: dict[str, Any] = field(default_factory=dict)
    analyzed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
