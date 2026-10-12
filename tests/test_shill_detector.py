"""Shill-campaign detection (operator directive 2026-10-12)."""

from __future__ import annotations

import pytest

from rugbrain.ingest_sources.alt_ingest_shill import analyze


@pytest.mark.parametrize(
    "text,category",
    [
        ("100x gem guaranteed returns", "guaranteed_returns"),
        ("presale is live, whitelist only", "presale_promo"),
        ("DM me on telegram for the alpha group", "telegram_cta"),
        ("exit liquidity rug incoming, honeypot", "rug_or_drain"),
    ],
)
def test_flags_shill_categories(text, category):
    sig = analyze(text)
    assert sig is not None and category in sig.categories


def test_neutral_news_is_clean():
    assert analyze("SEC approves spot bitcoin ETF after long deliberation") is None
    assert analyze("") is None


def test_contract_address_is_extracted_and_upweighted():
    sig = analyze("pump 100x contract 0x" + "a" * 40)
    assert sig is not None
    assert sig.addresses == ("0x" + "a" * 40,)
    assert "contract_promo" in sig.categories


def test_bare_token_without_shill_language_stays_clean():
    assert analyze("contract 0x" + "b" * 40) is None
