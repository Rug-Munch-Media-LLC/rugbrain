"""Tests for rmi_rag.sentiment_engine — crypto-specific sentiment analysis."""

from __future__ import annotations

from rmi_rag.sentiment_engine import (
    SentimentResult,
    analyze_text_sentiment,
)


def test_analyze_bearish_rug_pull():
    """Strong bearish keywords produce bearish sentiment."""
    result = analyze_text_sentiment("This token is definitely a rug pull scam ponzi!")
    assert result.sentiment == "bearish"
    assert result.confidence >= 0.5
    assert "rug pull" in result.signals or "rug" in result.signals
    assert "scam" in result.signals
    assert result.scores["bearish"] > result.scores["bullish"]


def test_analyze_bullish_moon():
    """Bullish crypto slang produces bullish sentiment."""
    result = analyze_text_sentiment("based af, this token is going to the moon! 🚀 parbolic!")
    assert result.sentiment == "bullish"
    assert result.confidence >= 0.3
    assert "based af" in result.signals or "moon" in result.signals
    assert result.scores["bullish"] > result.scores["bearish"]


def test_analyze_neutral():
    """Neutral text returns neutral sentiment."""
    result = analyze_text_sentiment("The Ethereum network processes transactions.")
    assert result.sentiment == "neutral"
    assert result.confidence > 0


def test_analyze_empty_text():
    """Empty text returns neutral with zero confidence."""
    result = analyze_text_sentiment("")
    assert result.sentiment == "neutral"
    assert result.confidence == 0.0
    assert result.signals == []


def test_analyze_mixed_signals():
    """Dominant signal category wins."""
    # More bearish keywords than bullish
    result = analyze_text_sentiment("rug pull scam moon based")
    # "rug pull" + "scam" > "based" + "moon" in weight
    assert result.sentiment == "bearish"
    assert result.confidence > 0


def test_analyze_text_length_tracked():
    """Text length is recorded."""
    result = analyze_text_sentiment("scam token")
    assert result.text_length == len("scam token")


def test_analyze_scores_structure():
    """Scores dict has all three categories."""
    result = analyze_text_sentiment("scam rug pull")
    assert "bearish" in result.scores
    assert "bullish" in result.scores
    assert "neutral" in result.scores
    assert isinstance(result.scores["bearish"], float)
    assert result.scores["bearish"] > 0


def test_analyze_no_false_positives():
    """Normal text doesn't falsely trigger sentiment."""
    result = analyze_text_sentiment("hello world foo bar")
    assert result.sentiment == "neutral"
    assert result.confidence < 0.3


def test_intensity_boosts():
    """Intensity markers boost confidence for the same keywords."""
    plain = analyze_text_sentiment("scam")
    intensified = analyze_text_sentiment("scam !! BEWARE")
    assert intensified.confidence >= plain.confidence


def test_deduplication():
    """Signals list has no duplicates."""
    result = analyze_text_sentiment("rug rug rug scam scam")
    assert len(result.signals) == len(set(result.signals))


def test_sentiment_result_types():
    """SentimentResult is properly typed."""
    r = analyze_text_sentiment("rug pull")
    assert isinstance(r, SentimentResult)
    assert isinstance(r.sentiment, str)
    assert isinstance(r.confidence, float)
    assert isinstance(r.signals, list)
    assert isinstance(r.scores, dict)
    assert isinstance(r.text_length, int)
    assert isinstance(r.analyzed_at, str)
