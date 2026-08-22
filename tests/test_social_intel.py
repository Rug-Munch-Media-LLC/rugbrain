"""Tests for rugbrain.social_intel — X/Reddit/Farcaster social intelligence."""
from __future__ import annotations

import importlib as _il
from typing import Any

import httpx
import pytest
import respx

social_intel = _il.import_module("rugbrain.social_intel")

mod = social_intel
from rugbrain.ingest_common import doc_from_article

REDDIT_PAYLOAD = {
    "data": {
        "children": [
            {
                "data": {
                    "title": "Solana upgrade shipped",
                    "permalink": "/r/solana/comments/abc/solana_upgrade_shipped/",
                    "selftext": "Great news for validators.",
                    "created_utc": 1754300000,
                    "score": 123,
                    "num_comments": 45,
                    "author": "satoshi",
                }
            }
        ]
    }
}

HUBBLE_CAST = {
    "messages": [
        {
            "data": {
                "fid": 3,
                "timestamp": 1754300000,
                "castBody": {"text": "gm from farcaster, bullish on new chains"},
                "reactions": {"count": 7},
            },
            "hash": "0xabc",
        }
    ]
}


# ── Analysis helpers ─────────────────────────────────────────────────────


def _doc(title: str, content: str, source: str = "x", **meta: Any) -> Any:
    return doc_from_article(
        title=title,
        content=content,
        url=f"https://x/{source}/{title}",
        source=source,
        collection=mod.SOCIAL_COLLECTION,
        metadata=meta,
    )


def test_analyze_sentiment_positive_negative() -> None:
    bullish = _doc("BTC bullish moon rally", "strong gains, buy signal")
    bearish = _doc("rug pull hack", "crash dump, loss")
    assert mod.analyze_sentiment([bullish]) > 0
    assert mod.analyze_sentiment([bearish]) < 0
    assert mod.analyze_sentiment([]) == 0.0
    mixed = mod.analyze_sentiment([bullish, bearish])
    assert -1.0 <= mixed <= 1.0


def test_trending_topics_counts() -> None:
    docs = [
        _doc("solana upgrade", "solana validators solana"),
        _doc("ethereum roadmap", "ethereum roadmap"),
        _doc("solana another", "solana again"),
        _doc("solana plus", "solana more"),
    ]
    topics = mod.trending_topics(docs, limit=3)
    assert topics[0][0] == "solana"
    assert topics[0][1] >= 3


def test_influential_posts_ranks_by_engagement() -> None:
    low = _doc("low", "x", score=1, num_comments=1)
    high = _doc("high", "x", score=500, num_comments=100)
    ranked = mod.influential_posts([low, high])
    assert ranked[0].title == "high"


# ── Fetching ─────────────────────────────────────────────────────────────


@respx.mock
async def test_fetch_x_posts_via_searxng(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "SEARXNG_URL", "http://127.0.0.1:8089")
    respx.get(url__regex=r"http://127\.0\.0\.1:8089/search.*").respond(
        200,
        json={"results": [{"title": "tweet", "url": "https://x.com/h/status/1", "content": "gm"}]},
    )
    async with httpx.AsyncClient() as client:
        docs = await mod.fetch_x_posts(client, ("VitalikButerin",))
    assert len(docs) == 1
    assert docs[0].source == "x:VitalikButerin"
    assert docs[0].category == "crypto_twitter"


@respx.mock
async def test_fetch_reddit_posts() -> None:
    respx.get("https://www.reddit.com/r/solana/new.json").respond(200, json=REDDIT_PAYLOAD)
    async with httpx.AsyncClient() as client:
        docs = await mod.fetch_reddit_posts(client, ("solana",), limit=25)
    assert len(docs) == 1
    assert docs[0].source == "reddit:solana"
    assert docs[0].metadata["score"] == 123
    assert docs[0].url.startswith("https://www.reddit.com")


@respx.mock
async def test_fetch_reddit_posts_handles_failure() -> None:
    respx.get("https://www.reddit.com/r/ethdev/new.json").respond(403, json={})
    async with httpx.AsyncClient() as client:
        docs = await mod.fetch_reddit_posts(client, ("ethdev",), limit=25)
    assert docs == []


@respx.mock
async def test_fetch_farcaster_casts_recent_casts() -> None:
    respx.get("http://127.0.0.1:2281/v1/info").respond(200, json={"version": "1.0"})
    respx.get("http://127.0.0.1:2281/v1/recent-casts").respond(200, json=HUBBLE_CAST)
    async with httpx.AsyncClient() as client:
        docs = await mod.fetch_farcaster_casts(client, ("3",))
    assert len(docs) == 1
    assert docs[0].source == "farcaster"
    assert docs[0].metadata["reactions"] == 7


@respx.mock
async def test_fetch_farcaster_casts_falls_back_to_fid() -> None:
    respx.get("http://127.0.0.1:2281/v1/info").respond(200, json={"version": "1.0"})
    respx.get("http://127.0.0.1:2281/v1/recent-casts").respond(500, json={})
    respx.get("http://127.0.0.1:2281/v1/castsByFid").respond(200, json=HUBBLE_CAST)
    async with httpx.AsyncClient() as client:
        docs = await mod.fetch_farcaster_casts(client, ("3",))
    assert len(docs) == 1


@respx.mock
async def test_fetch_farcaster_casts_hubble_down() -> None:
    respx.get("http://127.0.0.1:2281/v1/info").respond(503, json={})
    async with httpx.AsyncClient() as client:
        docs = await mod.fetch_farcaster_casts(client, ("3",))
    assert docs == []


async def test_collect_social_intel_dry_run(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def fake_x(client: Any, accounts: Any) -> Any:
        return [_doc("x post", "content", source="x:h")]

    async def fake_reddit(client: Any, subreddits: Any, limit: int = 25) -> Any:
        return [_doc("reddit post", "content", source="reddit:solana", score=10)]

    async def fake_farcaster(client: Any, fids: Any) -> Any:
        return [_doc("cast", "content", source="farcaster", reactions=2)]

    monkeypatch.setattr(mod, "fetch_x_posts", fake_x)
    monkeypatch.setattr(mod, "fetch_reddit_posts", fake_reddit)
    monkeypatch.setattr(mod, "fetch_farcaster_casts", fake_farcaster)
    result = await mod.collect_social_intel(dry_run=True, limit=50, accounts=("h",))
    assert result["posts"] == 4  # 3 posts + 1 summary digest
    assert result["inserted"] == 0
    assert "sentiment" in result
    assert isinstance(result["trending"], list)
