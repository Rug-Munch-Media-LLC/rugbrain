"""Prediction-market corpus: doc shape, freshness contract, bounded fetch.

No live calls. Every test builds its own market fixture; the fetch test injects
a fake httpx client so CI never touches Polymarket.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rugbrain.ingest_sources.prediction_markets import HARD_CAP, PredictionMarkets
from rugbrain.prediction_markets_docs import market_to_doc
from rugbrain.prediction_markets_freshness import (
    LABEL_CURRENT,
    LABEL_STALE,
    STALE_AFTER_HOURS,
    annotate,
    freshness,
)

FETCHED = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

MARKET = {
    "id": "m-1",
    "question": "Will a spot SOL ETF be approved in 2026?",
    "slug": "will-a-spot-sol-etf-be-approved-in-2026",
    "condition_id": "0xabc",
    "outcomes": [
        {"name": "Yes", "price": 0.42, "probability_pct": 42.0},
        {"name": "No", "price": 0.58, "probability_pct": 58.0},
    ],
    "volume24hr": 1_250_000.0,
    "liquidity": 90_000.0,
    "end_date": "2026-12-31T23:59:00Z",
    "category": "crypto",
}


def test_freshness_is_current_inside_window():
    state = freshness(FETCHED, now=FETCHED + timedelta(hours=1))
    assert state["label"] == LABEL_CURRENT
    assert state["stale"] is False


def test_freshness_is_stale_past_window():
    state = freshness(FETCHED, now=FETCHED + timedelta(hours=STALE_AFTER_HOURS + 0.5))
    assert state["label"] == LABEL_STALE
    assert state["stale"] is True


def test_annotate_labels_stale_odds_in_the_answer():
    text = annotate("Yes 42.0% / No 58.0%", FETCHED, now=FETCHED + timedelta(hours=9))
    assert "[STALE]" in text
    assert "not current" in text


def test_annotate_marks_current_odds():
    text = annotate("Yes 42.0% / No 58.0%", FETCHED, now=FETCHED + timedelta(minutes=30))
    assert "[CURRENT]" in text


def test_doc_carries_odds_provenance_and_fetched_at():
    doc = market_to_doc(MARKET, FETCHED, now=FETCHED)
    assert doc is not None
    assert doc.doc_id == "polymarket_live:will-a-spot-sol-etf-be-approved-in-2026"
    assert doc.doc_type == "prediction_market"
    assert doc.source == "polymarket_live"
    assert "Yes 42.0%" in doc.content
    assert "2026-12-31T23:59:00Z" in doc.content
    assert doc.metadata["fetched_at"] == "2026-10-25T12:00:00Z"
    assert doc.metadata["odds_freshness"] == LABEL_CURRENT
    assert doc.metadata["source_url"].startswith("https://polymarket.com/market/")
    assert doc.metadata["probabilities"] == {"Yes": 42.0, "No": 58.0}


def test_doc_text_is_searchable_by_natural_question_tokens():
    doc = market_to_doc(MARKET, FETCHED, now=FETCHED)
    lowered = doc.content.lower()
    for token in ("sol etf", "prediction market", "crypto", "odds"):
        assert token in lowered


def test_stale_slice_is_labelled_in_content_not_presented_as_current():
    doc = market_to_doc(MARKET, FETCHED, now=FETCHED + timedelta(hours=48))
    assert "[STALE]" in doc.content
    assert "[CURRENT]" not in doc.content
    assert doc.metadata["odds_freshness"] == LABEL_STALE


def test_market_without_question_or_slug_is_dropped():
    assert market_to_doc({"slug": "x"}, FETCHED) is None
    assert market_to_doc({"question": "q"}, FETCHED) is None


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        if self.exc:
            raise self.exc
        return _FakeResponse(self.payload)


@pytest.mark.asyncio
async def test_fetch_bounds_the_slice_and_normalises_docs():
    client = _FakeClient([MARKET])
    docs = await PredictionMarkets()._fetch(client, 10_000)
    assert len(docs) == 1
    assert client.calls[0][1]["limit"] == HARD_CAP


@pytest.mark.asyncio
async def test_fetch_fails_open_on_upstream_error():
    docs = await PredictionMarkets()._fetch(_FakeClient(exc=RuntimeError("boom")), 5)
    assert docs == []


def test_stale_market_is_labelled_not_current():
    """A 7h-old snapshot must be labelled stale, never presented as current odds."""
    from datetime import datetime, timedelta, timezone

    from rugbrain.prediction_markets_docs import market_to_doc
    from rugbrain.prediction_markets_freshness import annotate

    now = datetime.now(timezone.utc)
    fetched = now - timedelta(hours=7)
    market = {"question": "Will X protocol ship by date Y?",
              "slug": "x-protocol-ship-by-date-y",
              "outcomes": [{"name": "Yes", "price": 0.61}, {"name": "No", "price": 0.39}],
              "volume24hr": 1234.0, "end_date": "2027-01-01", "category": "crypto"}
    doc = market_to_doc(market, fetched, now=now)
    assert doc is not None
    assert doc.metadata["odds_freshness"] == "stale"
    assert doc.metadata["odds_age_hours"] == 7.0
    assert "Odds snapshot [STALE]" in doc.content
    assert "stale after 6.0h" in doc.content
    rendered = annotate(doc.content, fetched, now=now)
    assert rendered.rstrip().endswith("not current")
    assert "[CURRENT]" not in rendered
