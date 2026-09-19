"""Health check data for the LunarCrush social sentiment engine."""

from __future__ import annotations

import time
from typing import Any


async def social_sentiment_health() -> dict[str, Any]:
    """Return health check data for the LunarCrush social sentiment engine."""
    from rugbrain.monitors import social_sentiment as _pkg

    now = time.time()
    last_poll = _pkg._health_state.get("last_successful_poll", 0)
    time_since_poll = now - last_poll

    ok = (
        _pkg.API_KEY != ""
        and _pkg._health_state.get("status") == "running"
        and time_since_poll < _pkg.POLL_INTERVAL * 3  # Allow 3 missed intervals
    )

    return {
        "name": "lunarcrush_social_sentiment",
        "ok": ok,
        "status": _pkg._health_state.get("status", "unknown"),
        "api_key_configured": _pkg.API_KEY != "",
        "last_poll_ts": last_poll,
        "time_since_last_poll_s": round(time_since_poll, 1),
        "consecutive_failures": _pkg._health_state.get("consecutive_failures", 0),
        "total_polls": _pkg._health_state.get("total_polls", 0),
        "coins_tracked": len(_pkg._coin_stats),
        "coins_in_history": len(_pkg._sentiment_history),
        "feed_posts_cached": len(_pkg._social_feed),
        "recent_alerts": len(_pkg._recent_alerts),
        "poll_interval_s": _pkg.POLL_INTERVAL,
    }
