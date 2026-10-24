"""Sentiment and ticker extraction: deterministic, confidence-bearing.

The scorer is a lexicon on purpose — identical input must give identical output,
because a stored sentiment is quoted by the bot and the website.
"""

from __future__ import annotations

from rugbrain.ingest_sources.content_sources import analyze_item

HACK = (
    "A $SOL bridge was drained for 12M after an exploit in the validator set. "
    "The team called it a hack and halted deposits."
)
RALLY = (
    "Bitcoin surges past resistance as ETF inflows record a fourth week of gains; "
    "analysts turn bullish on $BTC."
)


def test_bearish_item_scores_negative_with_confidence() -> None:
    result = analyze_item(HACK, title="Bridge drained")
    sentiment = result["sentiment"]
    assert sentiment["label"] == "bearish"
    assert sentiment["score"] < -0.2
    assert sentiment["confidence"] > 0.3


def test_bullish_item_scores_positive() -> None:
    sentiment = analyze_item(RALLY, title="BTC breakout")["sentiment"]
    assert sentiment["label"] == "bullish"
    assert sentiment["score"] > 0.2


def test_cashtags_and_named_tickers_are_both_extracted() -> None:
    assert analyze_item(HACK)["tickers"] == ["SOL"]
    assert "BTC" in analyze_item(RALLY)["tickers"]


def test_no_signal_is_neutral_at_low_confidence() -> None:
    sentiment = analyze_item("Quarterly report published on the website.")["sentiment"]
    assert sentiment["label"] == "neutral"
    assert sentiment["score"] == 0.0
    assert sentiment["confidence"] == 0.2


def test_entities_come_from_the_shared_extractor() -> None:
    entities = analyze_item("0x" + "a" * 40 + " holds funds on ethereum")["entities"]
    assert isinstance(entities, dict)
    assert "addresses" in entities


def test_scoring_is_deterministic() -> None:
    assert analyze_item(HACK) == analyze_item(HACK)
