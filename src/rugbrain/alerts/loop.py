"""Long-running poller loop."""

from __future__ import annotations

import asyncio
import logging

from . import config
from .poller import poll_once

log = logging.getLogger("rugbrain.alerts")


async def run_alert_poller(dry_run: bool = False) -> None:
    """Long-running poller: tick every TICK_INTERVAL, poll sources on their
    own rate-limit cadence."""
    log.info(
        "alert_poller_starting interval=%ss tick=%ss",
        config.POLL_INTERVAL,
        config.TICK_INTERVAL,
    )
    while True:
        try:
            results = await poll_once(dry_run=dry_run)
            log.info(
                "alert_poller_pass new=%d detected=%d sources=%s",
                results.get("total_new", 0),
                results.get("total_detected", 0),
                list(results["sources"].keys()),
            )
        except Exception as e:
            log.warning("alert_poller_pass_failed: %s", e)
        await asyncio.sleep(config.TICK_INTERVAL)
