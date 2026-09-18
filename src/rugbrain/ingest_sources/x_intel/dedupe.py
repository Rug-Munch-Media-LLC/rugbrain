"""Dedup helpers for the X ingest pipeline (in-batch + cross-run, fail-open)."""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.ingest_sources.x_intel.config import MIN_TWEET_CHARS, RAG_COLLECTION

logger = logging.getLogger("rugbrain.x_intel")


async def dedupe_seen(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by tweet id (in-batch) + Redis seen-set (cross-run).

    Redis down → everything treated as fresh (fail-open).
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for t in tweets:
        tid = str(t.get("tweet_id") or "")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        unique.append(t)
    try:
        from rugbrain.compat import get_redis_async

        r = await get_redis_async()
    except Exception as exc:
        logger.debug("xintel:dedupe_redis_unavailable err=%s", str(exc)[:120])
        return unique
    fresh: list[dict[str, Any]] = []
    for t in unique:
        key = f"xintel:seen:{t['tweet_id']}"
        if await r.set(key, 1, nx=True, ex=7 * 86400):
            fresh.append(t)
    return fresh


def tweet_to_record(tweet: dict[str, Any]) -> dict[str, Any] | None:
    """Typed ingest record for one normalized tweet, routed to ``collection``.

    Returns None for dust (too-short text or missing URL) so the pipeline can
    filter with a single list comprehension.
    """
    text = str(tweet.get("text") or "").strip()
    handle = str(tweet.get("handle") or "").strip().lstrip("@")
    url = str(tweet.get("url") or "").strip()
    if len(text) < MIN_TWEET_CHARS or not url:
        return None
    return {
        "content": f"[X] @{handle}: {text[:4000]}",
        "collection": RAG_COLLECTION,
        "severity": "medium",
        "source": f"x.com/{handle}" if handle else "x.com",
        "metadata": {
            "kind": "x_tweet",
            "handle": handle,
            "tweet_id": tweet.get("tweet_id"),
            "url": url,
            "ts": str(tweet.get("created_at") or ""),
            "like_count": tweet.get("like_count", 0),
            "retweet_count": tweet.get("retweet_count", 0),
            "lane": tweet.get("source", "getxapi"),
        },
    }


def tweets_to_docs(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map normalized tweets → ingest docs, dropping dust (fail-open)."""
    return [rec for t in tweets if (rec := tweet_to_record(t)) is not None]


__all__ = ["dedupe_seen", "tweet_to_record", "tweets_to_docs"]
