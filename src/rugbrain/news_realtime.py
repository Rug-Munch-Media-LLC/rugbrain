"""
Real-time news story updates — auto-cluster new articles and publish.

Part 5 of the Ground News / CryptoPanic-beating news aggregation system.

When new articles are ingested, ``process_news_batch()``:

1. embeds the new article titles with Qwen3-Embedding-4B,
2. matches each against existing stories (stored in Qdrant ``news_v2``)
   by title-embedding cosine similarity — matching stories are extended,
   new stories are created,
3. reranks + re-scores every affected story and upserts it,
4. publishes a ``news.story.updated`` event to NATS (Redis pub/sub
   fallback) so downstream consumers (webhooks, bots, dashboards) react
   in real time.

A background ``NewsStoryUpdater`` task can also poll the raw ``news_articles``
collection periodically and feed any unclustered articles into
``process_news_batch()`` — the "when new articles are ingested, auto-cluster
with existing stories" loop for pipelines that don't call the batch hook.

NATS subject: ``news.story.updated`` (NATS_URL empty -> Redis pub/sub).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime
from typing import Any

from rugbrain.news_clustering import (
    CLUSTER_THRESHOLD,
    Story,
    StorySource,
    build_story,
    cluster_articles,
)
from rugbrain.news_ranking import (
    find_matching_story,
    rank_story,
    store_story,
)

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

NEWS_STORY_SUBJECT = os.getenv("NEWS_STORY_SUBJECT", "news.story.updated")
UPDATER_POLL_SECONDS = float(os.getenv("NEWS_UPDATER_POLL_SECONDS", "60"))
UPDATER_MAX_ARTICLES = int(os.getenv("NEWS_UPDATER_MAX_ARTICLES", "50"))
#: Raw article collection polled by the background updater.
NEWS_ARTICLES_COLLECTION = os.getenv("NEWS_ARTICLES_COLLECTION", "news_articles")


# ── Event publishing ─────────────────────────────────────────────────────


async def publish_story_update(story: Story) -> bool:
    """Publish a ``news.story.updated`` event (NATS, Redis pub/sub fallback).

    Reuses ``rugbrain.alerts.AlertBus`` so the transport is NATS when
    ``NATS_URL`` is set and Redis pub/sub otherwise. Best-effort: returns
    False (never raises) when no bus is reachable.
    """
    try:
        from rugbrain.alerts import AlertBus

        bus = AlertBus(subject=NEWS_STORY_SUBJECT)
        await bus.connect()
        payload = {
            "event": "news.story.updated",
            "story_id": story.id,
            "title": story.title,
            "summary": story.summary,
            "source_count": len(story.sources),
            "composite_score": story.composite_score,
            "topics": story.topics,
            "updated_at": story.updated_at,
        }
        ok = await bus.publish_payload(payload)
        await bus.close()
        if not ok:
            logger.debug("news_realtime: publish skipped (no bus connected)")
        return ok
    except Exception as exc:
        logger.warning("news_realtime: publish failed: %s", exc)
        return False


# ── Batch processing ─────────────────────────────────────────────────────


def _article_from_source(src: StorySource, story_id: str) -> dict[str, Any]:
    """Convert a StorySource back into a raw article dict (for merging)."""
    return {
        "id": src.article_id,
        "title": src.title,
        "url": src.url,
        "source": src.source,
        "summary": src.summary,
        "published_at": src.published_at,
        "domain": src.domain,
    }


def _merge_article_into_story(story: Story, article: dict[str, Any]) -> Story:
    """Add one article to an existing story and rebuild derived fields.

    Avoids duplicating sources that already exist (matched by URL). The
    story id, title and summary are preserved; sources/timeline/distributions
    are recomputed from the merged set.
    """
    existing_urls = {s.url for s in story.sources}
    url = str(article.get("url") or "")
    if url and url in existing_urls:
        return story
    merged_articles = [_article_from_source(s, story.id) for s in story.sources]
    merged_articles.append(article)
    rebuilt = build_story(merged_articles, story_id=story.id)
    rebuilt.title = story.title  # keep the anchor title
    rebuilt.summary = story.summary
    rebuilt.created_at = story.created_at
    return rebuilt


async def process_news_batch(
    articles: list[dict[str, Any]],
    threshold: float = CLUSTER_THRESHOLD,
) -> list[Story]:
    """Auto-cluster a batch of new articles into (possibly existing) stories.

    For each article, the title is embedded and matched against the
    existing story collection; a close match extends that story, otherwise a
    new story is created from the article. All affected stories are
    reranked, scored, persisted to Qdrant and published to NATS.

    Returns the list of updated/created stories.
    """
    if not articles:
        return []
    updated: list[Story] = []
    # New articles that don't match an existing story — clustered together.
    unmatched: list[dict[str, Any]] = []

    for article in articles:
        title = str(article.get("title") or "").strip()
        if not title:
            continue
        try:
            from rugbrain.embeddings import get_embedding

            emb = await get_embedding(title)
        except Exception as exc:
            logger.warning("news_realtime: embed failed for %r (%s)", title, exc)
            unmatched.append(article)
            continue
        existing = await find_matching_story(title, emb, threshold)
        if existing is not None:
            merged = _merge_article_into_story(existing, article)
            ranked = await rank_story(merged)
            await store_story(ranked)
            await publish_story_update(ranked)
            updated.append(ranked)
        else:
            unmatched.append(article)

    if unmatched:
        fresh = await cluster_articles(unmatched, threshold=threshold)
        for story in fresh:
            ranked = await rank_story(story)
            await store_story(ranked)
            await publish_story_update(ranked)
            updated.append(ranked)

    logger.info(
        "news_realtime: processed %d articles -> %d story updates", len(articles), len(updated)
    )
    return updated


# ── Background updater (poll unclustered articles) ───────────────────────


_updater_task: asyncio.Task[Any] | None = None
_updater_lock = asyncio.Lock()
_updater_stats: dict[str, Any] = {
    "runs": 0,
    "articles_processed": 0,
    "stories_updated": 0,
    "last_run": "",
    "last_error": "",
}


async def _collect_unclustered_articles() -> list[dict[str, Any]]:
    """Scroll the raw articles collection for points without a story_id.

    Uses the payload ``story_id`` marker (set by the updater after an
    article is clustered). Falls back to the ``news_articles`` collection;
    returns at most ``UPDATER_MAX_ARTICLES`` articles.
    """
    try:
        from rugbrain.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        collected: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        try:
            offset: str | None = None
            for _ in range(5):
                results, offset = await store.scroll(
                    NEWS_ARTICLES_COLLECTION,
                    limit=100,
                    offset=offset,
                )
                if not results:
                    break
                for r in results:
                    metadata = r.get("metadata") or {}
                    if metadata.get("story_id"):
                        continue
                    url = str(metadata.get("url") or "")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    title = str(metadata.get("title") or "")
                    if not title:
                        continue
                    collected.append(
                        {
                            "id": r.get("id"),
                            "title": title,
                            "url": url,
                            "source": str(metadata.get("source") or ""),
                            "summary": r.get("content", "")[:1200],
                            "published_at": metadata.get("published_at"),
                            "category": str(metadata.get("category") or ""),
                        }
                    )
                    if len(collected) >= UPDATER_MAX_ARTICLES:
                        break
                if len(collected) >= UPDATER_MAX_ARTICLES or not offset:
                    break
        finally:
            await store.close()
        return collected
    except Exception as exc:
        logger.warning("news_realtime: collect unclustered failed: %s", exc)
        return []


async def _mark_articles_clustered(
    articles: list[dict[str, Any]], story_ids: dict[str, str]
) -> None:
    """Write the story_id marker back onto raw article payloads.

    Best-effort — the next updater pass simply re-clusters unmarked
    articles, so a failed marker write is self-healing.
    """
    if not story_ids:
        return
    try:
        import httpx

        from rugbrain.qdrant_store import _resolve_qdrant_url

        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            for article in articles:
                story_id = story_ids.get(
                    str(article.get("url") or "") or str(article.get("id") or "")
                )
                if not story_id:
                    continue
                point_id = article.get("id")
                if not point_id:
                    continue
                r = await client.post(
                    f"/collections/{NEWS_ARTICLES_COLLECTION}/points/payload",
                    json={
                        "payload": {"metadata": {"story_id": story_id}},
                        "points": [point_id],
                    },
                )
                if r.status_code not in (200, 201):
                    logger.debug(
                        "news_realtime: mark story_id failed status=%d",
                        r.status_code,
                    )
    except Exception as exc:
        logger.debug("news_realtime: mark clustered failed: %s", exc)


async def _updater_pass() -> list[Story]:
    """One updater pass: cluster unclustered articles, mark + publish."""
    articles = await _collect_unclustered_articles()
    if not articles:
        return []
    stories = await process_news_batch(articles)
    # Map article -> story id for the marker write-back.
    story_by_url: dict[str, str] = {}
    story_by_id: dict[str, str] = {}
    for story in stories:
        for src in story.sources:
            if src.url:
                story_by_url[src.url] = story.id
            if src.article_id:
                story_by_id[src.article_id] = story.id
    mapping = {**story_by_url, **story_by_id}
    await _mark_articles_clustered(articles, mapping)
    return stories


async def _updater_loop() -> None:
    global _updater_stats
    while True:
        try:
            stories = await _updater_pass()
            _updater_stats["runs"] += 1
            _updater_stats["stories_updated"] += len(stories)
            _updater_stats["last_run"] = datetime.now(UTC).isoformat()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _updater_stats["last_error"] = str(exc)[:300]
            logger.warning("news_realtime: updater pass failed: %s", exc)
        await asyncio.sleep(UPDATER_POLL_SECONDS)


async def start_news_story_updater() -> bool:
    """Start the background story updater (idempotent)."""
    global _updater_task
    async with _updater_lock:
        if _updater_task is not None and not _updater_task.done():
            return True
        _updater_task = asyncio.create_task(_updater_loop(), name="news-story-updater")
        return True


async def stop_news_story_updater() -> None:
    """Cancel the background story updater (idempotent)."""
    global _updater_task
    task = _updater_task
    _updater_task = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def is_news_story_updater_running() -> bool:
    """True when the background updater task is active."""
    return _updater_task is not None and not _updater_task.done()


def news_story_updater_stats() -> dict[str, Any]:
    """Return updater statistics (for /health and dashboards)."""
    return dict(_updater_stats)


__all__ = [
    "NEWS_STORY_SUBJECT",
    "UPDATER_POLL_SECONDS",
    "is_news_story_updater_running",
    "news_story_updater_stats",
    "process_news_batch",
    "publish_story_update",
    "start_news_story_updater",
    "stop_news_story_updater",
]
