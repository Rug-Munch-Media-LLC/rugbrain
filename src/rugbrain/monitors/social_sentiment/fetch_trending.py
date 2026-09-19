"""Fetch trending coins with social stats from LunarCrush."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from .config import DEFAULT_LIMIT

logger = logging.getLogger("rugbrain.monitors.social_sentiment")


async def fetch_trending_coins(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Fetch trending coins with social stats from LunarCrush.

    GET /public/coins/trending/v2
    Returns: list of {symbol, name, social_score, sentiment, mentions_24h, ...}
    """
    from rugbrain.monitors import social_sentiment as _pkg

    path = "/public/coins/trending/v2"
    data = await _pkg._api_get(path, {"limit": min(limit, 100)})
    if not data or not isinstance(data, dict):
        return []

    coins = data.get("data", [])
    if not isinstance(coins, list):
        return []

    results = []
    for coin in coins:
        if not isinstance(coin, dict):
            continue
        symbol = (coin.get("symbol") or "").upper()
        if not symbol:
            continue

        sentiment_raw = coin.get("sentiment")
        # LunarCrush sentiment can be a string ("positive") or a score
        sentiment_score = _pkg._parse_sentiment_score(sentiment_raw)

        mentions_24h = int(coin.get("mentions_24h", 0))
        interactions_24h = int(coin.get("interactions_24h", 0))
        contributors_24h = int(coin.get("contributors_24h", 0))

        entry: dict[str, Any] = {
            "symbol": symbol,
            "name": coin.get("name", ""),
            "sentiment_score": sentiment_score,
            "sentiment_label": _pkg.classify_sentiment(sentiment_score),
            "social_volume": mentions_24h,
            "social_interactions": interactions_24h,
            "social_score": int(coin.get("social_score", 0)),
            "social_dominance": float(coin.get("social_dominance", 0)),
            "contributors_24h": contributors_24h,
            "market_cap": float(coin.get("market_cap", 0)),
            "price": float(coin.get("price", 0)),
            "price_change_24h": float(coin.get("price_change_24h", 0)),
            "galaxy_score": float(coin.get("galaxy_score", 0)),
            "volatility": float(coin.get("volatility", 0)),
            "scraped_at": datetime.now(UTC).isoformat(),
        }

        # Store in per-coin stats
        _pkg._coin_stats[symbol] = entry

        # Track history
        now_ts = time.time()
        _pkg._sentiment_history[symbol].append(
            {
                "timestamp": now_ts,
                "sentiment": sentiment_score,
            }
        )
        _pkg._volume_history[symbol].append(
            {
                "timestamp": now_ts,
                "volume": mentions_24h,
            }
        )

        results.append(entry)

    logger.debug("lc_trending_fetched count=%d limit=%d", len(results), limit)
    return results
