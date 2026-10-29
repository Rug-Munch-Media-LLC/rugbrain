"""Unified News Feed — podcast, newsletter and news content in one endpoint.

Queries the sources that actually exist (``podcast_episodes``,
``newsletters``, ``news_articles_8b``) through :mod:`rugbrain.news_feed_query`,
which owns the fetch, the quality gate and the dedupe. This module is the
HTTP surface only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from rugbrain.news_feed_query import DEFAULT_QUERY, fetch_feed_rows, select_feed_rows

logger = logging.getLogger(__name__)

news_feed_router = APIRouter(prefix="/api/v1/feed", tags=["news-feed"])


@news_feed_router.get("/all")
async def get_unified_news_feed(
    q: str = Query(default="", description="Search query across all sources"),
    limit: int = Query(default=20, ge=1, le=100),
    sources: str = Query(
        default="podcast,newsletter,news",
        description="Comma-separated sources to include",
    ),
    category: str | None = Query(default=None, description="Filter by category"),
    min_date: str | None = Query(default=None, description="ISO date filter (YYYY-MM-DD)"),
) -> dict[str, Any]:
    """Get unified news feed across podcasts, newsletters, and news."""
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    per_source = max(1, limit // max(1, len(source_list)) + 1)
    try:
        rows = await fetch_feed_rows(q or DEFAULT_QUERY, source_list, per_source)
    except Exception as exc:
        logger.warning("news_feed_failed %s", str(exc)[:200])
        return {"results": [], "total": 0, "error": str(exc)[:200]}

    selected = select_feed_rows(rows, category, min_date, limit)
    return {
        "results": selected,
        "total": len(selected),
        "query": q,
        "sources_queried": source_list,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@news_feed_router.get("/sources")
async def list_sources() -> dict[str, Any]:
    """List all tracked content sources."""
    from rugbrain.newsletter_ingester import NEWSLETTERS
    from rugbrain.podcast_ingester import CRYPTO_PODCASTS
    from rugbrain.youtube_ingester import YT_CHANNELS

    return {
        "youtube": {
            "total": len(YT_CHANNELS),
            "categories": sorted(set(c for _, _, c in YT_CHANNELS)),
        },
        "podcasts": {
            "total": len(CRYPTO_PODCASTS),
        },
        "newsletters": {
            "total": len(NEWSLETTERS),
            "categories": sorted(set(c for _, _, _, c in NEWSLETTERS)),
        },
        "total_sources": len(YT_CHANNELS) + len(CRYPTO_PODCASTS) + len(NEWSLETTERS),
    }
