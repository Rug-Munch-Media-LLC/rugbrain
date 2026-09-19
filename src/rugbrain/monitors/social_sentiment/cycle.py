"""One comprehensive LunarCrush social sentiment polling cycle."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("rugbrain.monitors.social_sentiment")


async def social_sentiment_full_cycle() -> dict[str, Any]:
    """Run one comprehensive LunarCrush social sentiment polling cycle."""
    from rugbrain.monitors import social_sentiment as _pkg

    # Fetch trending coins and social feed in parallel
    trending_coins, feed = await asyncio.gather(
        _pkg.fetch_trending_coins(limit=_pkg.DEFAULT_LIMIT),
        _pkg.fetch_social_feed(limit=20),
    )

    # Update global stores
    _pkg._trending_coins = trending_coins
    if feed:
        _pkg._social_feed = feed

    # Also fetch detailed stats for top coins (if trending returned any)
    top_symbols = [c["symbol"] for c in trending_coins[:10]]
    if top_symbols:
        detailed_tasks = [_pkg.fetch_coin_social(sym) for sym in top_symbols]
        await asyncio.gather(*detailed_tasks)
        # Detailed results are merged into _coin_stats via fetch_coin_social

    # Generate alerts from trending coin data
    alerts = await _pkg._generate_alerts(trending_coins)

    # Dispatch alerts
    for alert in alerts:
        _pkg._recent_alerts.append(alert)
        if alert["severity"] in ("high", "critical"):
            await _pkg._dispatch_alert(alert)

    # Compute aggregate stats
    total_vol_24h = sum(c.get("social_volume", 0) for c in trending_coins)
    avg_sentiment = (
        sum(c.get("sentiment_score", 50) for c in trending_coins) / len(trending_coins)
        if trending_coins
        else 50.0
    )

    # Positive / neutral / negative breakdown
    pos_count = sum(1 for c in trending_coins if c.get("sentiment_score", 50) > 60)
    neg_count = sum(1 for c in trending_coins if c.get("sentiment_score", 50) < 40)
    neutral_count = len(trending_coins) - pos_count - neg_count

    # Top movers (by 24h change in social volume)
    top_gainers = sorted(
        [c for c in trending_coins if c.get("price_change_24h", 0) > 0],
        key=lambda c: c.get("price_change_24h", 0),
        reverse=True,
    )[:5]
    top_losers = sorted(
        [c for c in trending_coins if c.get("price_change_24h", 0) < 0],
        key=lambda c: c.get("price_change_24h", 0),
    )[:5]

    # Market cap correlation (avg sentiment of top 10 vs bottom 10 by market cap)
    sorted_by_mcap = sorted(trending_coins, key=lambda c: c.get("market_cap", 0), reverse=True)
    top10_sentiment = (
        sum(c.get("sentiment_score", 50) for c in sorted_by_mcap[:10]) / 10
        if len(sorted_by_mcap) >= 10
        else None
    )
    bottom10_sentiment = (
        sum(c.get("sentiment_score", 50) for c in sorted_by_mcap[-10:]) / 10
        if len(sorted_by_mcap) >= 10
        else None
    )

    # Update health
    _pkg._health_state["last_successful_poll"] = time.time()
    _pkg._health_state["consecutive_failures"] = 0
    _pkg._health_state["coins_tracked"] = len(_pkg._coin_stats)
    _pkg._health_state["total_polls"] += 1
    _pkg._health_state["status"] = "running"

    cycle_result: dict[str, Any] = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "coins_tracked": len(trending_coins),
        "coins_in_history": len(_pkg._coin_stats),
        "total_social_volume_24h": total_vol_24h,
        "avg_sentiment": round(avg_sentiment, 1),
        "sentiment_breakdown": {
            "positive": pos_count,
            "neutral": neutral_count,
            "negative": neg_count,
            "bullish_extreme": sum(
                1
                for c in trending_coins
                if c.get("sentiment_score", 50) >= _pkg.SENTIMENT_BULLISH_THRESHOLD
            ),
            "bearish_extreme": sum(
                1
                for c in trending_coins
                if c.get("sentiment_score", 50) <= _pkg.SENTIMENT_BEARISH_THRESHOLD
            ),
        },
        "market_cap_correlation": {
            "top10_avg_sentiment": round(top10_sentiment, 1) if top10_sentiment else None,
            "bottom10_avg_sentiment": round(bottom10_sentiment, 1) if bottom10_sentiment else None,
            "sentiment_gap": (
                round(top10_sentiment - bottom10_sentiment, 1)
                if top10_sentiment is not None and bottom10_sentiment is not None
                else None
            ),
        },
        "top_gainers": top_gainers,
        "top_losers": top_losers,
        "alerts_generated": len(alerts),
        "alerts": alerts[:10],
        "feed_posts": len(_pkg._social_feed),
    }

    logger.info(
        "lc_cycle coins=%d total_vol=%d avg_sent=%.1f alerts=%d",
        len(trending_coins),
        total_vol_24h,
        avg_sentiment,
        len(alerts),
    )

    return cycle_result
