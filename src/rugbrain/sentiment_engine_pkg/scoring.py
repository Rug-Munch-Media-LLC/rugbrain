"""Text-level scoring: keyword extraction, modifiers, ``analyze_text_sentiment``.

Moved verbatim from ``sentiment_engine.py`` (synchronous, no IO).
"""

from __future__ import annotations

from rugbrain.sentiment_engine_pkg.lexicon import (
    _BEARISH_KEYWORDS,
    _BULLISH_KEYWORDS,
    _INTENSITY_MARKERS,
    _NEUTRAL_KEYWORDS,
    _STRENGTH_MODIFIERS,
)
from rugbrain.sentiment_engine_pkg.models import SentimentResult

# ── Keyword extraction ───────────────────────────────────────────────────────


def _extract_keywords(text_lower: str, keyword_map: dict[str, float]) -> tuple[list[str], float]:
    """Find weighted keywords in text. Returns (matched_keys, total_weight)."""
    matched: list[str] = []
    total = 0.0
    # Sort by key length descending so longer phrases match before substrings.
    for keyword in sorted(keyword_map, key=len, reverse=True):
        if keyword in text_lower:
            count = text_lower.count(keyword)
            weight = keyword_map[keyword] * count
            total += weight
            matched.append(keyword)
    return matched, total


def _apply_modifiers(text_raw: str, base_score: float) -> float:
    """Adjust score using strength/intensity modifiers found in text."""
    modified = base_score
    # Strength modifiers
    for modifier, factor in _STRENGTH_MODIFIERS.items():
        if modifier in text_raw.lower():
            modified *= factor
    # Intensity markers
    for marker, factor in _INTENSITY_MARKERS.items():
        if marker in text_raw:
            modified *= factor
    return min(max(modified, 0.0), 1.0)  # Clamp to [0, 1]


# ── Public API ───────────────────────────────────────────────────────────────


def analyze_text_sentiment(text: str) -> SentimentResult:
    """Score the sentiment of *text* using crypto-specific keyword detection.

    Scans for bearish (rug, dump, ponzi, exploit, ...), bullish (moon,
    based, ath, ...), and neutral/cautionary patterns, then combines them
    into a structured ``SentimentResult`` with confidence and signals.

    Returns a ``SentimentResult`` named tuple-like dataclass with
    ``sentiment`` ("bearish"|"bullish"|"neutral"), ``confidence`` (0-1),
    ``signals`` (matched keywords), and ``scores`` (raw category weights).

    >>> analyze_text_sentiment("This token is definitely a rug pull!")
    SentimentResult(sentiment='bearish', confidence=0.9, signals=['rug pull'], ...)
    """
    if not text or not text.strip():
        return SentimentResult(
            sentiment="neutral",
            confidence=0.0,
            signals=[],
            scores={"bearish": 0.0, "bullish": 0.0, "neutral": 0.0},
            text_length=0,
        )

    text_lower = text.lower()
    text_raw = text

    # Extract keyword signals.
    bearish_keys, bearish_raw = _extract_keywords(text_lower, _BEARISH_KEYWORDS)
    bullish_keys, bullish_raw = _extract_keywords(text_lower, _BULLISH_KEYWORDS)
    neutral_keys, neutral_raw = _extract_keywords(text_lower, _NEUTRAL_KEYWORDS)

    # Apply strength/intensity modifiers to each score.
    # Keep raw values for comparison (they carry relative ordering), then
    # clamp the winner to [0,1] for the confidence output.
    bearish_score = _apply_modifiers(text_raw, bearish_raw)
    bullish_score = _apply_modifiers(text_raw, bullish_raw)
    neutral_score = _apply_modifiers(text_raw, neutral_raw)

    # Determine dominant sentiment (compare clamped — but use a higher ceiling
    # so heavily-weighted texts still show the correct winner).
    # We compare *raw* category totals (before clamping) so a heavily-bearish
    # text with many signals still beats a mildly-bullish one.
    if bearish_raw > bullish_raw and bearish_raw > neutral_raw and bearish_raw > 0.1:
        sentiment = "bearish"
        confidence = bearish_score
    elif bullish_raw > bearish_raw and bullish_raw > neutral_raw and bullish_raw > 0.1:
        sentiment = "bullish"
        confidence = bullish_score
    elif neutral_raw > 0.05:
        sentiment = "neutral"
        confidence = neutral_score
    else:
        # No strong signals — lean neutral with low confidence.
        sentiment = "neutral"
        confidence = 0.15

    # Clamp and round scores for output.
    scores = {
        "bearish": round(min(bearish_score, 1.0), 4),
        "bullish": round(min(bullish_score, 1.0), 4),
        "neutral": round(min(neutral_score, 1.0), 4),
    }

    # Collect signals (deduplicated, in order of highest weight).
    all_signals = sorted(
        set(bearish_keys + bullish_keys + neutral_keys),
        key=lambda k: (
            _BEARISH_KEYWORDS.get(k, 0) + _BULLISH_KEYWORDS.get(k, 0) + _NEUTRAL_KEYWORDS.get(k, 0)
        ),
        reverse=True,
    )

    return SentimentResult(
        sentiment=sentiment,
        confidence=round(min(confidence, 1.0), 4),
        signals=all_signals[:20],
        scores=scores,
        text_length=len(text),
    )
