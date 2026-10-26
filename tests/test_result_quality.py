"""Tests for the feed/search quality gates (``result_quality``)."""

from __future__ import annotations

from rugbrain.result_quality import dedupe_by_content, is_publishable_news

_ARTICLE = (
    "Solana Foundation taps Binance, Polygon vets to drive institutional adoption "
    "and payments. The foundation said the hires will support tokenised funds and "
    "settlement pilots with regulated partners across Asia and Europe."
)
_CHROME = (
    "Digital Asset Data & Intelligence for Institutions html.w-mod-js:not(.w-mod-ix3) "
    ":is(.section_cm-trusted .cm-trusted_logo-content) {visibility: hidden !important;} "
    '!function(o,c){var n=c.documentElement,t="w-mod-";n.className+=t+"js"}'
)


def test_article_is_publishable() -> None:
    assert is_publishable_news({"content": _ARTICLE}) is True


def test_page_chrome_is_rejected() -> None:
    assert is_publishable_news({"content": _CHROME}) is False


def test_short_fragment_is_rejected() -> None:
    assert is_publishable_news({"content": "Current Ethereum Price: $2,571.19"}) is False


def test_missing_content_is_rejected() -> None:
    assert is_publishable_news({}) is False


def test_dedupe_keeps_first_and_collapses_whitespace_variants() -> None:
    items = [
        {"content": _ARTICLE, "collection": "news_v2_8b"},
        {"content": _ARTICLE.replace(" ", "  "), "collection": "education_8b"},
        {"content": _ARTICLE + " Different tail.", "collection": "social_intel_8b"},
    ]
    out = dedupe_by_content(items)
    assert len(out) == 2
    assert out[0]["collection"] == "news_v2_8b"


def test_dedupe_drops_empty_content() -> None:
    assert dedupe_by_content([{"content": ""}, {"content": "   "}]) == []
