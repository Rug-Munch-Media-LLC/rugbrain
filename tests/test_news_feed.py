"""Tests for the news feed API endpoints (rugbrain.news_feed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from rugbrain.news_clustering import Story, StorySource


@pytest.fixture
def client():
    from fastapi import FastAPI

    from rugbrain.news_feed import news_feed_router

    app = FastAPI()
    app.include_router(news_feed_router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _story(**overrides) -> Story:
    now = datetime.now(UTC)
    sources = [
        StorySource(
            article_id="s1",
            title="Ethereum ETF approved by SEC",
            url="https://coindesk.com/1",
            source="coindesk.com",
            domain="coindesk.com",
            credibility=95,
            leaning="center",
            published_at=(now - timedelta(hours=2)).isoformat(),
            summary="The SEC approved spot ether ETFs in a landmark decision.",
            sentiment="positive",
            rerank_score=0.9,
        ),
        StorySource(
            article_id="s2",
            title="Ethereum ETF gets green light",
            url="https://theblock.co/2",
            source="theblock.co",
            domain="theblock.co",
            credibility=94,
            leaning="center",
            published_at=(now - timedelta(hours=3)).isoformat(),
            summary="Regulators approved ether ETFs after months of review.",
            sentiment="neutral",
            rerank_score=0.7,
        ),
    ]
    return Story(
        id="story-1",
        title="Ethereum ETF approved by SEC",
        summary="The SEC approved spot ether ETFs in a landmark decision.",
        sources=sources,
        primary_source_id="s1",
        bias_distribution={"left": 0, "center": 2, "right": 0, "unknown": 0},
        sentiment_distribution={"positive": 1, "neutral": 1, "negative": 0},
        topics=["ethereum", "sec"],
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    ).model_copy(update=overrides)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/news/feed
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_news_feed_empty_without_store(client, monkeypatch):
    """No stories in Qdrant -> 200 with empty stories (fail-open)."""

    async def fake_ranked_feed(topic=None, min_quality=70.0, limit=20):
        return []

    import rugbrain.news_feed as nf

    monkeypatch.setattr(nf, "ranked_feed", fake_ranked_feed)
    resp = await client.get("/api/v1/news/feed", params={"topic": "security", "min_quality": 70})
    assert resp.status_code == 200
    data = resp.json()
    assert data["stories"] == []
    assert data["total"] == 0
    assert data["topic"] == "security"


@pytest.mark.asyncio
async def test_news_feed_returns_ranked_stories(client, monkeypatch):
    story = _story(composite_score=88.0)
    story2 = _story(id="story-2", composite_score=75.0)

    async def fake_ranked_feed(topic=None, min_quality=70.0, limit=20):
        return [story2, story]

    import rugbrain.news_feed as nf

    monkeypatch.setattr(nf, "ranked_feed", fake_ranked_feed)
    resp = await client.get("/api/v1/news/feed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    first = data["stories"][0]
    assert first["id"] == "story-2"
    assert first["source_count"] == 2
    assert "bias" in first
    assert "sentiment" in first
    assert first["primary_source"] == "Ethereum ETF approved by SEC"


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/news/story/{id}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_news_story_detail(client, monkeypatch):
    story = _story()

    async def fake_load_story(story_id):
        return story

    import rugbrain.news_feed as nf

    monkeypatch.setattr(nf, "load_story", fake_load_story)
    resp = await client.get("/api/v1/news/story/story-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "story-1"
    assert data["primary_source_id"] == "s1"
    assert len(data["sources"]) == 2
    # is_primary flag + bias chart present.
    assert any(src["is_primary"] for src in data["sources"])
    assert set(data["bias_chart"]["distribution"]) == {"left", "center", "right", "unknown"}
    assert data["bias_chart"]["known_total"] == 2
    assert set(data["sentiment_chart"]["distribution"]) == {"positive", "neutral", "negative"}


@pytest.mark.asyncio
async def test_news_story_detail_404(client, monkeypatch):
    async def fake_load_story(story_id):
        return None

    import rugbrain.news_feed as nf

    monkeypatch.setattr(nf, "load_story", fake_load_story)
    resp = await client.get("/api/v1/news/story/missing")
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/news/trending
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_news_trending_velocity_sorted(client, monkeypatch):
    hot = _story(id="hot", velocity=12.5, composite_score=80.0)
    cold = _story(id="cold", velocity=0.5, composite_score=90.0)

    async def fake_trending(limit=20):
        return [hot, cold]

    import rugbrain.news_feed as nf

    monkeypatch.setattr(nf, "trending_stories", fake_trending)
    resp = await client.get("/api/v1/news/trending")
    assert resp.status_code == 200
    data = resp.json()
    assert [s["id"] for s in data["stories"]] == ["hot", "cold"]
    assert data["stories"][0]["velocity"] == 12.5


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/news/topics
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_news_topics_counts(client, monkeypatch):
    async def fake_topic_counts(limit=50):
        return {"ethereum": 5, "bitcoin": 3, "security": 1}

    import rugbrain.news_feed as nf

    monkeypatch.setattr(nf, "topic_counts", fake_topic_counts)
    resp = await client.get("/api/v1/news/topics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["topics"][0] == {"topic": "ethereum", "count": 5}
    assert data["total"] == 3
