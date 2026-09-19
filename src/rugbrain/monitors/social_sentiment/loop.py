"""Continuous background loop for LunarCrush social sentiment monitoring."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("rugbrain.monitors.social_sentiment")


async def social_sentiment_loop() -> None:
    """Run LunarCrush social sentiment monitoring in a continuous background loop.

    Polls every POLL_INTERVAL seconds (default: 300 = 5 minutes).
    Imports cleanly as::

        from rugbrain.monitors.social_sentiment import social_sentiment_loop
    """
    from rugbrain.monitors import social_sentiment as _pkg

    logger.info("lc_monitor_started interval=%ds", _pkg.POLL_INTERVAL)

    if not _pkg.API_KEY:
        logger.warning("lc_monitor_no_api_key LUNARCRUSH_API_KEY not set — monitoring disabled")
        _pkg._health_state["status"] = "disabled"
        # Still run the loop but it'll log a warning each cycle
        # Allows graceful handling if key is added at runtime

    while True:
        try:
            result = await _pkg.social_sentiment_full_cycle()
            if result["alerts_generated"] > 0:
                logger.info(
                    "lc_alerts count=%d extremes=%s spikes=%s reversals=%s",
                    result["alerts_generated"],
                    result["sentiment_breakdown"].get("bullish_extreme", 0),
                    sum(1 for a in result["alerts"] if a.get("type") == "volume_spike"),
                    sum(1 for a in result["alerts"] if a.get("type") == "sentiment_reversal"),
                )
        except Exception as exc:
            _pkg._health_state["consecutive_failures"] += 1
            _pkg._health_state["last_error"] = str(exc)[:200]
            logger.warning(
                "lc_cycle_failed attempt=%d err=%s",
                _pkg._health_state["consecutive_failures"],
                str(exc)[:80],
            )

        await asyncio.sleep(_pkg.POLL_INTERVAL)
