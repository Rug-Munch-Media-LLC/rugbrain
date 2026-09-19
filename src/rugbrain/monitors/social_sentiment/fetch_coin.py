"""Fetch social stats for a specific coin from LunarCrush."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any


async def fetch_coin_social(symbol: str) -> dict[str, Any] | None:
    """Fetch social stats for a specific coin.

    GET /public/coin/{symbol}/v2
    Returns: dict with social stats including sentiment breakdown
    """
    from rugbrain.monitors import social_sentiment as _pkg

    path = f"/public/coin/{symbol}/v2"
    data = await _pkg._api_get(path)
    if not data or not isinstance(data, dict):
        return None

    coin_data = data.get("data")
    if isinstance(coin_data, list) and coin_data:
        coin = coin_data[0]
    elif isinstance(coin_data, dict):
        coin = coin_data
    else:
        return None

    if not isinstance(coin, dict):
        return None

    sentiment_raw = coin.get("sentiment")
    sentiment_score = _pkg._parse_sentiment_score(sentiment_raw)

    mentions_24h = int(coin.get("mentions_24h", 0))

    result: dict[str, Any] = {
        "symbol": symbol.upper(),
        "name": coin.get("name", ""),
        "sentiment_score": sentiment_score,
        "sentiment_label": _pkg.classify_sentiment(sentiment_score),
        "social_volume": mentions_24h,
        "social_interactions": int(coin.get("interactions_24h", 0)),
        "social_score": int(coin.get("social_score", 0)),
        "social_dominance": float(coin.get("social_dominance", 0)),
        "contributors_24h": int(coin.get("contributors_24h", 0)),
        "market_cap": float(coin.get("market_cap", 0)),
        "price": float(coin.get("price", 0)),
        "price_change_24h": float(coin.get("price_change_24h", 0)),
        "galaxy_score": float(coin.get("galaxy_score", 0)),
        "volatility": float(coin.get("volatility", 0)),
        "reddit_mentions": int(coin.get("reddit_mentions_24h", 0)),
        "twitter_mentions": int(coin.get("twitter_mentions_24h", 0)),
        "scraped_at": datetime.now(UTC).isoformat(),
    }

    # Update in-memory stores
    _pkg._coin_stats[symbol.upper()] = result

    now_ts = time.time()
    _pkg._sentiment_history[symbol.upper()].append(
        {
            "timestamp": now_ts,
            "sentiment": sentiment_score,
        }
    )
    _pkg._volume_history[symbol.upper()].append(
        {
            "timestamp": now_ts,
            "volume": mentions_24h,
        }
    )

    return result
