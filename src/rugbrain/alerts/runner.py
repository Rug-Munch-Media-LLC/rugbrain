"""Single-source poll + ingest orchestration."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config
from .bus import AlertBus
from .clickhouse import ClickHouseAlertLogger
from .helpers import _enabled
from .state import AlertState

log = logging.getLogger("rugbrain.alerts")


async def _run_source(
    name: str,
    client: httpx.AsyncClient,
    state: AlertState,
    bus: AlertBus,
    ch_logger: ClickHouseAlertLogger,
    now: float,
    dry_run: bool,
) -> dict[str, Any]:
    """Poll one source, ingest new alerts. Returns per-source stats."""
    from rugbrain import alerts as _pkg

    stats: dict[str, Any] = {"source": name, "detected": 0, "new": 0, "skipped_dup": 0}
    poller: Any = _pkg.SOURCE_POLLERS[name]
    if not _enabled(name):
        stats["disabled"] = True
        return stats
    last = await state.last_poll(name)
    if last is not None and (now - float(last)) < config.POLL_INTERVAL:
        stats["rate_limited"] = True
        return stats
    await state.set_last_poll(name, now)

    try:
        alerts = await poller(client, now)
    except Exception as e:
        log.warning("source=%s poll_error: %s", name, str(e)[:200])
        stats["error"] = str(e)[:200]
        return stats

    stats["detected"] = len(alerts)
    for alert in alerts:
        if await state.is_seen(alert.id):
            stats["skipped_dup"] += 1
            continue
        await state.mark_seen(alert.id)
        stats["new"] += 1
        log.info(
            "alert_detected source=%s title=%s severity=%s amount=%s",
            alert.source,
            alert.title[:100],
            alert.severity,
            f"${alert.amount_lost_usd:,.0f}" if alert.amount_lost_usd else "unknown",
        )
        if dry_run:
            continue
        embedding = await _pkg._embed(alert.text())
        if embedding is None:
            log.warning("alert_embed_failed title=%s", alert.title[:80])
            continue
        await _pkg.store_alert(alert, embedding)
        await bus.publish(alert)
        ch_logger.log(alert)
    return stats
