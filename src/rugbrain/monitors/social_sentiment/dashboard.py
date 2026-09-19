"""Full social sentiment state for dashboard API consumption."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def get_social_sentiment_state() -> dict[str, Any]:
    """Return full social sentiment state for dashboard API consumption."""
    from rugbrain.monitors import social_sentiment as _pkg

    # Build per-coin 24h changes
    coin_details = {}
    for symbol, stats in _pkg._coin_stats.items():
        change = _pkg.calculate_24h_change(symbol)
        coin_details[symbol] = {
            **stats,
            **change,
            "sentiment_trend": _pkg.get_sentiment_label(stats.get("sentiment_score", 50)),
        }

    # Top by social volume
    top_by_volume = sorted(
        coin_details.values(),
        key=lambda c: c.get("social_volume", 0),
        reverse=True,
    )[:20]

    # Top by sentiment (most positive)
    top_by_sentiment = sorted(
        coin_details.values(),
        key=lambda c: c.get("sentiment_score", 50),
        reverse=True,
    )[:10]

    # Bottom by sentiment (most negative)
    bottom_by_sentiment = sorted(
        coin_details.values(),
        key=lambda c: c.get("sentiment_score", 50),
    )[:10]

    # Social volume spikes detected
    spikes = []
    for symbol, stats in _pkg._coin_stats.items():
        vol = stats.get("social_volume", 0)
        spike = _pkg.detect_social_volume_spike(symbol, vol)
        if spike:
            spikes.append(spike)

    return {
        "last_updated": datetime.now(UTC).isoformat(),
        "health": _pkg._health_state,
        "summary": {
            "coins_tracked": len(_pkg._coin_stats),
            "total_volume_24h": sum(c.get("social_volume", 0) for c in _pkg._coin_stats.values()),
            "avg_sentiment": (
                round(
                    sum(c.get("sentiment_score", 50) for c in _pkg._coin_stats.values())
                    / len(_pkg._coin_stats),
                    1,
                )
                if _pkg._coin_stats
                else None
            ),
        },
        "top_by_volume": top_by_volume,
        "top_by_sentiment": top_by_sentiment,
        "bottom_by_sentiment": bottom_by_sentiment,
        "volume_spikes": spikes[:10],
        "recent_alerts": list(_pkg._recent_alerts)[-20:],
        "trending_coins": _pkg._trending_coins[:20],
        "social_feed": _pkg._social_feed[:20],
        "coin_details": coin_details,
    }
