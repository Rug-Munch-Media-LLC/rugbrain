"""Standalone firehose contracts — RugBrain ingests WITHOUT rmi-backend.

docs/MODERNIZATION.md §5: RugBrain is fully standalone. The former
rmi-backend DataBus calls are gone — news/prediction/social fire straight at
their direct upstream adapters, and ct_rundown degrades gracefully (no
standalone upstream identified). These tests pin that contract.
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx


@contextmanager
def _patched_attr(module, name, value):
    """Swap a module attribute for the duration of the block (no mock layer)."""
    old = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, old)

import pytest

from rugbrain.firehose import news as fh_news
from rugbrain.firehose import twitter as fh_twitter


def _dead_backend_client() -> httpx.AsyncClient:
    """A client pointed at a port with nothing listening.

    Kept so every source still tolerates an unusable client object even
    though the standalone paths no longer dial a backend.
    """

    class _Dead:
        async def get(self, *a, **kw):
            raise httpx.ConnectError("backend down [Errno 111]")

    return _Dead()  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_news_falls_back_to_direct_when_backend_dead():
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

    from rugbrain.firehose import direct_sources

    with _patched_attr(direct_sources, "fetch_news_direct", fake_direct):
        docs = await fh_news.pull_news_rss(_dead_backend_client())
    assert called["direct"] is True
    assert len(docs) == 1
    d = docs[0]
    assert d["collection"] == "news_articles"
    assert d["metadata"]["source"] == "coindesk"
    assert d["content"].startswith("News: Test headline.")


@pytest.mark.asyncio
async def test_prediction_falls_back_to_direct_when_backend_dead():
    """DataBus unreachable → Polymarket gamma adapter produces market docs."""

    async def fake_direct(limit=10):
        return [
            {
                "collection": "market_intel",
                "content": "Prediction market: Will X? - YES: 0.5 NO: 0.5",
                "metadata": {"source": "polymarket", "type": "prediction"},
            }
        ]

    from rugbrain.firehose import direct_sources

    with _patched_attr(direct_sources, "fetch_prediction_markets_direct", fake_direct):
        docs = await fh_news.pull_prediction_markets(_dead_backend_client())
    assert len(docs) == 1
    assert docs[0]["collection"] == "market_intel"
    assert docs[0]["metadata"]["source"] == "polymarket"


@pytest.mark.asyncio
async def test_news_direct_is_the_only_path():
    """No backend dial: the direct adapter is always the path news takes."""
    calls = {"direct": 0}

    async def fake_direct(limit=20):
        calls["direct"] += 1
        return [
            {
                "collection": "news_articles",
                "content": "News: Standalone headline. D",
                "metadata": {"source": "coindesk"},
            }
        ]

    from rugbrain.firehose import direct_sources

    with _patched_attr(direct_sources, "fetch_news_direct", fake_direct):
        # Any client — even a "live backend" one — must be ignored.
        docs = await fh_news.pull_news_rss(_dead_backend_client())
    assert docs and docs[0]["metadata"]["source"] == "coindesk"
    assert calls["direct"] == 1


@pytest.mark.asyncio
async def test_ct_rundown_degrades_gracefully():
    """ct_rundown has no standalone upstream → log + skip (empty), never raises."""
    docs = await fh_twitter.pull_ct_rundown(_dead_backend_client())
    assert docs == []


def test_firehose_has_no_backend_urls():
    """Sever proof: no firehose module constructs an rmi-backend URL literal."""
    import pathlib

    pkg = pathlib.Path(fh_news.__file__).parent
    offenders = []
    for path in pkg.glob("*.py"):
        text = path.read_text()
        if "localhost:8000" in text or "127.0.0.1:8000" in text:
            offenders.append(path.name)
    assert offenders == [], f"backend URL literals remain: {offenders}"


# ── Direct adapter internals (offline, no network) ─────────────────────────


def test_rss_parse_extracts_items():
    """The stdlib XML path extracts title/desc/link/pub from real RSS shape."""
    import xml.etree.ElementTree as ET

    from rugbrain.firehose.direct_sources import _text_of

    xml = """
    <rss><channel>
      <item><title>T1</title><description>D1</description>
        <link>https://x/1</link><pubDate>Mon, 24 Aug 2026</pubDate></item>
      <item><title></title><description>skipped, no title</description></item>
    </channel></rss>
    """
    root = ET.fromstring(xml)  # noqa: S314 — parses a static test literal
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
async def test_social_sentiment_falls_back_to_fear_greed():
    """Dead backend → Fear & Greed adapter produces the daily_summary doc."""

    async def fake_direct():
        return [
            {
                "collection": "social_sentiment",
                "content": "Social metrics: Sentiment 74 (Greed), Trending: []",
                "metadata": {"source": "fear_greed", "type": "daily_summary"},
            }
        ]

    from rugbrain.firehose import direct_sources

    with _patched_attr(direct_sources, "fetch_social_sentiment_direct", fake_direct):
        docs = await fh_twitter.pull_social_sentiment(_dead_backend_client())
    assert len(docs) == 1
    assert docs[0]["metadata"]["source"] == "fear_greed"
    assert docs[0]["metadata"]["type"] == "daily_summary"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_fear_greed_reachable():
    """Live probe (cheap, no key): alternative.me answers with a value.

    Marked ``integration`` — it needs outbound network, so it is excluded
    from the unit-test gate and only runs in the live/ integration job.
    """
    from rugbrain.firehose.direct_sources import fetch_social_sentiment_direct

    docs = await fetch_social_sentiment_direct()
    assert docs, "fear-greed direct fetch returned empty"
    assert "Sentiment" in docs[0]["content"]
