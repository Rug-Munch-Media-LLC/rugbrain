"""Fetch the social feed / trending posts from LunarCrush."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("rugbrain.monitors.social_sentiment")


async def fetch_social_feed(limit: int = 20) -> list[dict[str, Any]]:
    """Fetch social feed / trending posts from LunarCrush.

    GET /public/feed
    Returns: list of posts with content, sentiment, engagement metrics
    """
    from rugbrain.monitors import social_sentiment as _pkg

    path = "/public/feed"
    data = await _pkg._api_get(path, {"limit": min(limit, 50)})
    if not data or not isinstance(data, dict):
        return []

    posts = data.get("data", [])
    if not isinstance(posts, list):
        return []

    results = []
    for post in posts:
        if not isinstance(post, dict):
            continue

        post_id = str(post.get("id", "")) or str(post.get("tweet_id", ""))
        if not post_id:
            continue

        # Dedup
        if post_id in _pkg._seen_posts:
            continue
        _pkg._seen_posts.add(post_id)
        if len(_pkg._seen_posts) > 100000:
            _pkg._seen_posts.clear()

        sentiment_raw = post.get("sentiment")
        sentiment_score = _pkg._parse_sentiment_score(sentiment_raw)

        entry: dict[str, Any] = {
            "id": post_id,
            "author": post.get("author", ""),
            "author_followers": int(post.get("author_followers", 0)),
            "content_preview": (post.get("title", "") or post.get("text", "") or "")[:280],
            "sentiment_score": sentiment_score,
            "sentiment_label": _pkg.classify_sentiment(sentiment_score),
            "likes": int(post.get("likes", 0)),
            "retweets": int(post.get("retweets", 0)),
            "replies": int(post.get("replies", 0)),
            "engagement_total": int(post.get("likes", 0))
            + int(post.get("retweets", 0))
            + int(post.get("replies", 0)),
            "coin_symbols": post.get("coins", []) or post.get("symbols", []),
            "created_at": post.get("created_at", ""),
            "scraped_at": datetime.now(UTC).isoformat(),
        }
        results.append(entry)

    logger.debug("lc_feed_fetched count=%d", len(results))
    return results
