"""Standalone firehose contracts — RugBrain ingests without rmi-backend.

docs/STANDALONE-MIGRATION-PLAN.md Phase 3: the DataBus endpoints on the
backend are the PRIMARY news/prediction sources, but the firehose must
degrade to DIRECT upstream fetches when the backend is down. These tests
pin that fallback contract and the doc shapes both paths produce.
"""

from __future__ import annotations

import httpx
import pytest

from rugbrain.firehose import news as fh_news
from rugbrain.firehose import twitter as fh_twitter
from rugbrain.firehose.direct_sources import (
    fetch_news_direct,
    fetch_prediction_markets_direct,
)


def _dead_backend_client() -> httpx.AsyncClient:
    """A client pointed at a port with nothing listening (backend absent)."""

    class _Dead:
        async def get(self, *a, **kw):  # noqa: ANN002, ANN003
            raise httpx.ConnectError("backend down [Errno 111]")

    return _Dead()  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_news_falls_back_to_direct_when_backend_dead(monkeypatch):
    """DataBus unreachable → direct RSS adapters produce well-formed docs."""
    called = {"direct": False}

    async def fake_direct(limit=20):
        called["direct"] = True
        assert limit == 20
        return [
            {
                "collection": "news_articles",
                "content": "News: Test headline. Body",
                "metadata": {
                    "source": "coindesk",
                    "url": "https://example.com/a",
                    "published": "Tue, 25 Aug 2026",
                    "category": "crypto",
                    "title": "Test headline",
                },
            }
        ]

    monkeypatch.setattr("rugbrain.firehose.direct_sources.fetch_news_direct", fake_direct)
    # patch the symbol as imported inside the function's module lookup path
    monkeypatch.setattr("rugbrain.firehose.direct_sources.fetch_news_direct", fake_direct)
    docs = await fh_news.pull_news_rss(_dead_backend_client())
    assert called["direct"] is True
    assert len(docs) == 1
    d = docs[0]
    assert d["collection"] == "news_articles"
    assert d["metadata"]["source"] == "coindesk"
    assert d["content"].startswith("News: Test headline.")


@pytest.mark.asyncio
async def test_prediction_falls_back_to_direct_when_backend_dead(monkeypatch):
    """DataBus unreachable → Polymarket gamma adapter produces market docs."""

    async def fake_direct(limit=10):
        return [
            {
                "collection": "market_intel",
                "content": "Prediction market: Will X? - YES: 0.5 NO: 0.5",
                "metadata": {"source": "polymarket", "type": "prediction"},
            }
        ]

    monkeypatch.setattr(
        "rugbrain.firehose.direct_sources.fetch_prediction_markets_direct",
        fake_direct,
    )
    docs = await fh_news.pull_prediction_markets(_dead_backend_client())
    assert len(docs) == 1
    assert docs[0]["collection"] == "market_intel"
    assert docs[0]["metadata"]["source"] == "polymarket"


@pytest.mark.asyncio
async def test_databus_primary_still_wins_when_up(monkeypatch):
    """When the backend answers, the direct adapters must NOT be called."""
    calls = {"direct": 0}

    class _Live:
        async def get(self, url, **kw):  # noqa: ANN002, ANN003
            class R:
                status_code = 200

                def json(self):
                    if "prediction" in url:
                        return {
                            "markets": [{"question": "Q?", "yes_price": "0.6", "no_price": "0.4"}]
                        }
                    return {
                        "articles": [
                            {
                                "title": "Backend headline",
                                "description": "D",
                                "source": "databus",
                            }
                        ]
                    }

            return R()

    async def fail_direct(*a, **kw):  # noqa: ANN002, ANN003
        calls["direct"] += 1
        return []

    monkeypatch.setattr("rugbrain.firehose.direct_sources.fetch_news_direct", fail_direct)
    monkeypatch.setattr(
        "rugbrain.firehose.direct_sources.fetch_prediction_markets_direct",
        fail_direct,
    )
    client = _Live()
    docs = await fh_news.pull_news_rss(client)  # type: ignore[arg-type]
    assert docs and docs[0]["metadata"]["source"] == "databus"
    assert calls["direct"] == 0


# ── Direct adapter internals (offline, no network) ─────────────────────────


def test_rss_parse_extracts_items():
    """The stdlib XML path extracts title/desc/link/pub from real RSS shape."""
    from rugbrain.firehose.direct_sources import _text_of
    import xml.etree.ElementTree as ET

    xml = """
    <rss><channel>
      <item><title>T1</title><description>D1</description>
        <link>https://x/1</link><pubDate>Mon, 24 Aug 2026</pubDate></item>
      <item><title></title><description>skipped, no title</description></item>
    </channel></rss>
    """
    root = ET.fromstring(xml)
    items = root.findall(".//item")
    assert len(items) == 2
    assert _text_of(items[0].find("title")) == "T1"
    assert _text_of(items[1].find("title")) == ""  # filtered by caller


def test_polymarket_prices_handle_string_encoded_list():
    """gamma API returns outcomePrices as a JSON-encoded STRING — must parse."""
    import json as _json

    prices = '["0.62","0.38"]'
    parsed = _json.loads(prices) if isinstance(prices, str) else prices
    assert parsed == ["0.62", "0.38"]


@pytest.mark.asyncio
async def test_social_sentiment_falls_back_to_fear_greed(monkeypatch):
    """Dead backend → Fear & Greed adapter produces the daily_summary doc."""
    from rugbrain.firehose.direct_sources import fetch_social_sentiment_direct

    async def fake_direct():
        return [
            {
                "collection": "social_sentiment",
                "content": "Social metrics: Sentiment 74 (Greed), Trending: []",
                "metadata": {"source": "fear_greed", "type": "daily_summary"},
            }
        ]

    monkeypatch.setattr(
        "rugbrain.firehose.direct_sources.fetch_social_sentiment_direct",
        fake_direct,
    )
    docs = await fh_twitter.pull_social_sentiment(_dead_backend_client())
    assert len(docs) == 1
    assert docs[0]["metadata"]["source"] == "fear_greed"
    assert docs[0]["metadata"]["type"] == "daily_summary"


@pytest.mark.asyncio
async def test_live_fear_greed_reachable():
    """Live probe (cheap, no key): alternative.me answers with a value."""
    from rugbrain.firehose.direct_sources import fetch_social_sentiment_direct

    docs = await fetch_social_sentiment_direct()
    assert docs, "fear-greed direct fetch returned empty"
    assert "Sentiment" in docs[0]["content"]
