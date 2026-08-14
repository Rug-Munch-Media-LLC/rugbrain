"""Tests for real-time news story updates (rugbrain.news_realtime)."""

from __future__ import annotations

import asyncio

import pytest

from rugbrain import news_realtime as nrt
from rugbrain.news_clustering import Story, StorySource

# ── publish_story_update ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_story_update_success(monkeypatch):
    """Publishes the story payload on the news.story.updated subject."""

    class FakeBus:
        def __init__(self, subject=None):
            self.subject = subject
            self.payload = None

        async def connect(self):
            return "redis"

        async def publish_payload(self, payload):
            self.payload = payload
            return True

        async def close(self):
            return None

    captured: dict = {}
    import rugbrain.alerts as alerts

    class FakeAlertBus(FakeBus):
        def __init__(self, subject=None):
            super().__init__(subject=subject)
            captured["subject"] = subject

    monkeypatch.setattr(alerts, "AlertBus", FakeAlertBus)
    story = Story(id="s1", title="Ethereum ETF approved", sources=[])
    ok = await nrt.publish_story_update(story)
    assert ok is True
    assert captured["subject"] == "news.story.updated"


@pytest.mark.asyncio
async def test_publish_story_update_fails_open(monkeypatch):
    """Bus connect failure -> publish returns False, never raises."""

    class BoomBus:
        def __init__(self, subject=None):
            pass

        async def connect(self):
            raise RuntimeError("no bus")

        async def publish_payload(self, payload):
            return False

        async def close(self):
            return None

    import rugbrain.alerts as alerts

    monkeypatch.setattr(alerts, "AlertBus", BoomBus)
    story = Story(id="s1", title="Ethereum ETF approved", sources=[])
    assert await nrt.publish_story_update(story) is False


# ── process_news_batch ────────────────────────────────────────────────────


def _article(article_id: str, title: str, url: str, published: str) -> dict:
    return {
        "id": article_id,
        "title": title,
        "url": url,
        "source": "coindesk.com",
        "summary": f"{title} — a test article summary with verifiable details.",
        "published_at": published,
    }


@pytest.mark.asyncio
async def test_process_news_batch_empty():
    assert await nrt.process_news_batch([]) == []


@pytest.mark.asyncio
async def test_process_news_batch_creates_story(monkeypatch):
    """New articles with no matching story create a fresh story."""
    articles = [
        _article(
            "a1", "Ethereum ETF approved by SEC", "https://coindesk.com/1", "2026-08-05T09:00:00Z"
        ),
        _article(
            "a2", "Ethereum ETF gets green light", "https://theblock.co/2", "2026-08-05T10:00:00Z"
        ),
    ]

    async def fake_find_matching(title, embedding, threshold):
        return None

    async def fake_cluster_articles(articles, threshold=0.72):
        from rugbrain.news_clustering import build_story

        return [build_story(articles)]

    async def fake_rank_story(story):
        story.composite_score = 88.0
        return story

    async def fake_store_story(story):
        return True

    async def fake_publish(story):
        return True

    import rugbrain.news_ranking as rk
    import rugbrain.news_realtime as mod

    monkeypatch.setattr(mod, "find_matching_story", fake_find_matching)
    monkeypatch.setattr(mod, "cluster_articles", fake_cluster_articles)
    monkeypatch.setattr(mod, "rank_story", fake_rank_story)
    monkeypatch.setattr(mod, "store_story", fake_store_story)
    monkeypatch.setattr(mod, "publish_story_update", fake_publish)
    monkeypatch.setattr(rk, "store_story", fake_store_story)

    # Embedding works (zero vector) so unmatched path is exercised.
    async def fake_get_embedding(text):
        return [1.0] * 4

    import rugbrain.embeddings as emb

    monkeypatch.setattr(emb, "get_embedding", fake_get_embedding)

    stories = await nrt.process_news_batch(articles)
    assert len(stories) == 1
    assert len(stories[0].sources) == 2


@pytest.mark.asyncio
async def test_process_news_batch_extends_existing(monkeypatch):
    """An article matching an existing story extends it (no new story)."""
    existing = Story(
        id="story-x",
        title="Ethereum ETF approved",
        summary="The SEC approved ether ETFs.",
        sources=[
            StorySource(
                article_id="s1",
                title="Ethereum ETF approved by SEC",
                url="https://coindesk.com/1",
                source="coindesk.com",
                summary="The SEC approved spot ether ETFs.",
            )
        ],
    )
    new_article = _article(
        "a2", "Ethereum ETF gets green light", "https://theblock.co/2", "2026-08-05T10:00:00Z"
    )

    async def fake_find_matching(title, embedding, threshold):
        return existing

    async def fake_rank_story(story):
        story.composite_score = 90.0
        return story

    async def fake_store_story(story):
        return True

    async def fake_publish(story):
        return True

    import rugbrain.news_realtime as mod

    monkeypatch.setattr(mod, "find_matching_story", fake_find_matching)
    monkeypatch.setattr(mod, "rank_story", fake_rank_story)
    monkeypatch.setattr(mod, "store_story", fake_store_story)
    monkeypatch.setattr(mod, "publish_story_update", fake_publish)

    async def fake_get_embedding(text):
        return [1.0] * 4

    import rugbrain.embeddings as emb

    monkeypatch.setattr(emb, "get_embedding", fake_get_embedding)

    stories = await nrt.process_news_batch([new_article])
    assert len(stories) == 1
    assert stories[0].id == "story-x"
    assert len(stories[0].sources) == 2  # merged, not duplicated


# ── merge helper ──────────────────────────────────────────────────────────


def test_merge_article_into_story_deduplicates_by_url():
    story = Story(
        id="story-x",
        title="Ethereum ETF approved",
        sources=[
            StorySource(
                article_id="s1",
                title="Ethereum ETF approved by SEC",
                url="https://coindesk.com/1",
                source="coindesk.com",
                summary="The SEC approved spot ether ETFs.",
            )
        ],
    )
    duplicate = _article(
        "s1-dup", "Ethereum ETF approved by SEC", "https://coindesk.com/1", "2026-08-05T09:00:00Z"
    )
    merged = nrt._merge_article_into_story(story, duplicate)
    assert len(merged.sources) == 1  # same URL -> skipped

    fresh = _article(
        "a2", "Ethereum ETF gets green light", "https://theblock.co/2", "2026-08-05T10:00:00Z"
    )
    merged2 = nrt._merge_article_into_story(story, fresh)
    assert len(merged2.sources) == 2
    assert merged2.id == "story-x"  # stable id preserved


# ── updater lifecycle ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_updater_start_stop():
    await nrt.start_news_story_updater()
    assert nrt.is_news_story_updater_running() is True
    await asyncio.sleep(0.05)
    stats = nrt.news_story_updater_stats()
    assert "runs" in stats
    await nrt.stop_news_story_updater()
    assert nrt.is_news_story_updater_running() is False
