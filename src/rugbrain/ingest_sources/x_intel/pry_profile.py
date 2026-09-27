"""Pry profile scrape — bio/followers/recent tweets for one X handle.

Split out of ``pry_lane`` (which owns the transport and the tweet primitive) so
neither module crosses the 200-line cap. Behaviour is unchanged; ``pry_lane``
re-exports ``scrape_profile`` so existing imports keep working.
"""

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Rug Munch Media LLC

from __future__ import annotations

import re
from typing import Any, cast

import httpx

from rugbrain.ingest_sources.x_intel.pry_cache import cache_get, cache_set
from rugbrain.ingest_sources.x_intel.pry_lane import (
    PRY_CACHE_PROFILE_S,
    ScrapeFn,
    _pry_scrape,
    scrape_tweet,
)
from rugbrain.ingest_sources.x_intel.pry_parse import parse_int

#: Cap on the recent-tweets fan-out: the profile page is cached for an hour and
#: every extra tweet is another paid/slow scrape.
MAX_RECENT_TWEETS = 5


async def scrape_profile(
    username: str,
    transport: httpx.AsyncBaseTransport | None = None,
    scrape: ScrapeFn = _pry_scrape,
) -> dict[str, Any] | None:
    """Scrape one X profile via Pry (FREE) → bio/followers/recent tweets.

    ``scrape`` is the DI seam (mirrors ``routing.pacing_active(redis_client)``):
    tests inject a stub instead of patching the module.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        return None
    cache_key = f"xintel:pry:profile:{username}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cast("dict[str, Any]", cached)
    data = await scrape(f"https://x.com/{username}", transport)
    if not data:
        return None
    markdown = str(data.get("markdown") or data.get("content") or "").strip()
    if not markdown:
        return None
    result: dict[str, Any] = {
        "username": username,
        "name": str((data.get("metadata") or {}).get("title") or "").strip() or username,
        "bio": _bio_from(markdown),
        "followers_count": _followers_from(markdown),
        "following_count": 0,
        "tweets_count": 0,
        "recent_tweets": await _recent_tweets(username, markdown, transport),
        "source": "pry",
    }
    await cache_set(cache_key, result, PRY_CACHE_PROFILE_S)
    return result


def _bio_from(markdown: str) -> str:
    """First prose-looking line of the profile body (skips the @handle line)."""
    for line in (ln.strip() for ln in markdown.splitlines()):
        if line and not re.fullmatch(r"@?[\w.]+", line) and len(line) >= 8:
            return line[:300]
    return ""


def _followers_from(markdown: str) -> int:
    """Follower count from the profile body, 0 when absent."""
    match = re.search(r"([\d.,]+[kKmM]?)\s*[Ff]ollowers", markdown)
    return parse_int(match.group(1)) if match else 0


async def _recent_tweets(
    username: str, markdown: str, transport: httpx.AsyncBaseTransport | None
) -> list[dict[str, Any]]:
    """Tweet ids linked from the profile page, de-duped, most recent first."""
    recent: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tweet_id in re.findall(rf"x\.com/{re.escape(username)}/status/(\d+)", markdown):
        if tweet_id in seen:
            continue
        seen.add(tweet_id)
        tweet = await scrape_tweet(f"https://x.com/{username}/status/{tweet_id}", transport)
        if tweet is not None:
            recent.append(tweet)
        if len(recent) >= MAX_RECENT_TWEETS:
            break
    return recent


__all__ = ["scrape_profile"]
