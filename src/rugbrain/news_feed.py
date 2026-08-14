"""
News feed API — ranked stories, story detail, trending, and topics.

Part 4 of the Ground News / CryptoPanic-beating news aggregation system.

Endpoints (mounted under ``/api/v1/news`` in main.py)::

    GET /api/v1/news/feed?topic=security&min_quality=70&limit=20
        Ranked feed of stories (composite score descending), optionally
        filtered by topic and minimum quality.
    GET /api/v1/news/story/{id}
        Full story with all source articles + bias/sentiment chart.
    GET /api/v1/news/trending
        Trending stories (velocity-based, min 2 sources).
    GET /api/v1/news/topics
        Available topics with story counts.

All endpoints fail open: when Qdrant is unreachable they return 200 with
an empty result set (matching the existing ``/api/v1/news/top`` behavior).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from rugbrain.news_ranking import (
    load_story,
    ranked_feed,
    topic_counts,
    trending_stories,
)

logger = logging.getLogger(__name__)

news_feed_router = APIRouter(prefix="/api/v1/news", tags=["news-feed"])


def _bias_chart(story: Any) -> dict[str, Any]:
    """Bias chart payload: counts + percentages across known leanings."""
    dist = dict(story.bias_distribution or {})
    total_known = sum(v for k, v in dist.items() if k != "unknown")
    total = sum(dist.values()) or 1
    return {
        "distribution": dist,
        "known_total": total_known,
        "percent": {k: round((v / total) * 100, 1) for k, v in dist.items()},
    }


def _sentiment_chart(story: Any) -> dict[str, Any]:
    dist = dict(story.sentiment_distribution or {})
    total = sum(dist.values()) or 1
    return {
        "distribution": dist,
        "percent": {k: round((v / total) * 100, 1) for k, v in dist.items()},
    }


def _story_feed_item(story: Any) -> dict[str, Any]:
    """Compact story representation for the feed list."""
    sources = story.sources or []
    primary = next(
        (s for s in sources if s.article_id == story.primary_source_id),
        sources[0] if sources else None,
    )
    return {
        "id": story.id,
        "title": story.title,
        "summary": story.summary,
        "source_count": len(sources),
        "composite_score": story.composite_score,
        "factual_consensus": story.factual_consensus,
        "topics": story.topics,
        "primary_source": primary.title if primary else "",
        "primary_credibility": primary.credibility if primary else 0,
        "updated_at": story.updated_at,
        "velocity": story.velocity,
        "bias": _bias_chart(story),
        "sentiment": _sentiment_chart(story),
    }


@news_feed_router.get("/feed")
async def news_feed(
    request: Request,
    topic: str | None = Query(
        default=None,
        description="Filter stories by topic (e.g. security, solana, ethereum)",
    ),
    min_quality: float = Query(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="Minimum composite quality score 0-100",
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Max stories to return"),
) -> dict[str, Any]:
    """Ranked news feed — stories sorted by composite quality descending.

    Optionally filtered by ``topic`` and ``min_quality``. Fails open to an
    empty list when Qdrant is unreachable::

        curl "http://localhost:8012/api/v1/news/feed?topic=security&min_quality=70&limit=20"
    """
    stories = await ranked_feed(topic=topic, min_quality=min_quality, limit=limit)
    return {
        "stories": [_story_feed_item(s) for s in stories],
        "total": len(stories),
        "topic": topic,
        "min_quality": min_quality,
        "limit": limit,
    }


@news_feed_router.get("/story/{story_id}")
async def news_story_detail(
    request: Request,
    story_id: str,
) -> dict[str, Any]:
    """Full story: all source articles + bias/sentiment charts + consensus.

    Raises 404 when the story does not exist (or Qdrant is unreachable —
    the endpoint cannot distinguish, matching fail-open philosophy by
    returning 404 for missing stories).
    """
    story = await load_story(story_id)
    if story is None:
        raise HTTPException(status_code=404, detail=f"story not found: {story_id}")

    sources = [
        {
            "article_id": s.article_id,
            "title": s.title,
            "url": s.url,
            "source": s.source,
            "domain": s.domain,
            "credibility": s.credibility,
            "tier": s.tier,
            "leaning": s.leaning,
            "published_at": s.published_at,
            "summary": s.summary,
            "sentiment": s.sentiment,
            "rerank_score": s.rerank_score,
            "is_primary": s.article_id == story.primary_source_id,
        }
        for s in (story.sources or [])
    ]
    return {
        "id": story.id,
        "title": story.title,
        "summary": story.summary,
        "primary_source_id": story.primary_source_id,
        "sources": sources,
        "timeline": story.timeline,
        "topics": story.topics,
        "factual_consensus": story.factual_consensus,
        "consensus_detail": story.consensus_detail,
        "bias_chart": _bias_chart(story),
        "sentiment_chart": _sentiment_chart(story),
        "axes": story.axes,
        "composite_score": story.composite_score,
        "velocity": story.velocity,
        "created_at": story.created_at,
        "updated_at": story.updated_at,
    }


@news_feed_router.get("/trending")
async def news_trending(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100, description="Max stories to return"),
) -> dict[str, Any]:
    """Trending stories — velocity-based (sources/hour), min 2 sources.

    Stories breaking across many sources quickly rank highest::

        curl "http://localhost:8012/api/v1/news/trending?limit=10"
    """
    stories = await trending_stories(limit=limit)
    return {
        "stories": [_story_feed_item(s) for s in stories],
        "total": len(stories),
        "limit": limit,
    }


@news_feed_router.get("/topics")
async def news_topics(
    request: Request,
) -> dict[str, Any]:
    """Available topics with story counts (descending, top 50)."""
    counts = await topic_counts()
    return {
        "topics": [{"topic": topic, "count": count} for topic, count in counts.items()],
        "total": len(counts),
    }
