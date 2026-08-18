"""Social Sentiment Ingestion — monitors X/Twitter + Discord for crypto signals.

Aggregates social media posts from:
1. **LunarCrush API** (free tier) — X/Twitter social metrics, sentiment scores
2. **Discord webhooks** — monitor specific Discord channels for scam reports
3. **Telegram channels** — monitor public Telegram groups for alpha/scam signals
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Config
LUNARCRUSH_KEY = os.getenv("LUNARCRUSH_API_KEY", "")
POLL_INTERVAL = int(os.getenv("SOCIAL_POLL_INTERVAL", "120"))  # 2 min
MAX_POSTS = int(os.getenv("SOCIAL_MAX_POSTS", "20"))

# Scam signal keywords
SCAM_KEYWORDS = [
    "rug", "honeypot", "scam", "exit scam", "pulled liquidity",
    "can't sell", "cannot sell", "locked liquidity", "hacked",
    "exploit", "drain", "fake", "malicious", "phishing",
]

# High-value crypto Twitter accounts to track
TRACKED_ACCOUNTS = [
    "zachxbt", "tayvano_", "samczsun", "officer_cia",
    "peckshield", "slowmist_team", "blocksec", "certik",
    "CyversAlerts", "Crypto_Slack", "0xCygaar", "bertcmiller",
]

# Rate limiting
_LUNARCRUSH_CACHE_TIME = 0.0
LUNARCRUSH_CACHE_TTL = 60  # cache for 1 minute


async def fetch_lunarcrush_social() -> list[dict[str, Any]]:
    """Fetch crypto social sentiment data from LunarCrush API."""
    global _LUNARCRUSH_CACHE_TIME
    now = time.time()

    if not LUNARCRUSH_KEY:
        return []

    if now - _LUNARCRUSH_CACHE_TIME < LUNARCRUSH_CACHE_TTL:
        return []

    _LUNARCRUSH_CACHE_TIME = now

    url = f"https://lunarcrush.com/api/v2/public/coins/trending/v2?limit={MAX_POSTS}&key={LUNARCRUSH_KEY}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []

    posts = []
    coins = data.get("data", []) if isinstance(data, dict) else []
    for coin in coins[:MAX_POSTS]:
        symbol = coin.get("symbol", "")
        name = coin.get("name", "")
        score = coin.get("social_score", 0)
        sentiment = coin.get("sentiment", "")
        posts.append({
            "source": "lunarcrush",
            "symbol": symbol,
            "name": name,
            "social_score": score,
            "sentiment": sentiment,
            "mentions_24h": coin.get("mentions_24h", 0),
            "contributors_24h": coin.get("contributors_24h", 0),
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })

    return posts


def _check_scam_keywords(text: str) -> list[str]:
    """Check if text contains scam-related keywords."""
    text_lower = text.lower()
    return [kw for kw in SCAM_KEYWORDS if kw in text_lower]


async def ingest_discord_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process an incoming Discord webhook payload for scam signals.

    Designed to be called from a Discord bot's webhook endpoint.
    """
    content = payload.get("content", "")
    author = payload.get("author", {}).get("username", "unknown")
    channel = payload.get("channel_name", payload.get("channel_id", "unknown"))

    scam_flags = _check_scam_keywords(content)
    mentions = [a for a in TRACKED_ACCOUNTS if f"@{a}" in content.lower()]

    return {
        "source": "discord",
        "author": author,
        "channel": channel,
        "content_preview": content[:200],
        "scam_flags": scam_flags,
        "mentions": mentions,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "severity": "high" if scam_flags else "info",
    }


async def social_sentiment_cycle() -> dict[str, Any]:
    """Run one full social sentiment scan cycle."""
    posts = await fetch_lunarcrush_social()

    # Check for scam signals in trending posts
    scam_signals = []
    for post in posts:
        flags = _check_scam_keywords(f"{post.get('name', '')} {post.get('symbol', '')}")
        if flags:
            scam_signals.append({**post, "scam_flags": flags})

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "trending_coins": len(posts),
        "scam_signals": len(scam_signals),
        "posts": posts[:10],
        "alerts": scam_signals[:5],
    }


async def social_sentiment_loop() -> None:
    """Run social sentiment monitoring in a continuous loop."""
    logger.info("social_sentiment_started interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await social_sentiment_cycle()
            if result["scam_signals"] > 0:
                logger.info(
                    "social_scam_signals count=%d",
                    result["scam_signals"],
                )
        except Exception as exc:
            logger.warning("social_sentiment_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
