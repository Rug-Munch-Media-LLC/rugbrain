"""news_now: corpus + web merge, published_at labelling, 24 h staleness, refusal."""

from __future__ import annotations

import importlib as _il
from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.live.envelope import ok_envelope, refuse

news_mod = _il.import_module("rugbrain.live.news")
news_now = news_mod.news_now


def _iso(delta_h: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=delta_h)).isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_news_merges_corpus_and_web_and_flags_stale(monkeypatch):
    async def corpus(topic, n):
        return [
            {"title": "old corpus", "url": "https://c1.example", "snippet": "s", "published_at": _iso(72), "source": "corpus"},
            {"title": "fresh corpus", "url": "https://c2.example", "snippet": "s", "published_at": _iso(1), "source": "corpus"},
        ]

    async def live(topic, n):
        return ok_envelope("searxng", [
            {"title": "web", "url": "https://w1.example", "snippet": "s", "published_at": _iso(0.5), "source": "searxng"},
        ])

    monkeypatch.setattr(news_mod, "web_search", live)
    env = await news_now("exploit", 10, corpus_fn=corpus)
    assert env.ok is True
    assert env.source == "corpus+web"
    by_title = {item["title"]: item for item in env.data}
    assert by_title["old corpus"]["stale"] is True
    assert by_title["fresh corpus"]["stale"] is False
    assert by_title["web"]["dated"] is True
    assert env.data[0]["published_at"] >= env.data[-1]["published_at"]


@pytest.mark.asyncio
async def test_news_refuses_when_every_source_empty(monkeypatch):
    async def corpus(topic, n):
        return []

    async def live(topic, n):
        return refuse("all_providers_failed", "searxng:timeout; pry-mcp:not_configured", "web_search")

    monkeypatch.setattr(news_mod, "web_search", live)
    env = await news_now("nobody covers this", 10, corpus_fn=corpus)
    assert env.ok is False
    assert env.data is None
    assert env.error["code"] == "all_sources_empty"
    assert "searxng:timeout" in env.error["reason"]
