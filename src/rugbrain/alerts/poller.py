"""Single full poll pass over all sources."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from .bus import AlertBus
from .clickhouse import ClickHouseAlertLogger
from .runner import _run_source
from .state import AlertState

log = logging.getLogger("rugbrain.alerts")


async def poll_once(dry_run: bool = False) -> dict[str, Any]:
    """Run a single full poll pass over all sources. Returns aggregate stats."""
    from rugbrain import alerts as _pkg

    state = AlertState()
    bus = AlertBus()
    ch_logger = ClickHouseAlertLogger()
    now = datetime.now(UTC).timestamp()
    results: dict[str, Any] = {"ran_at": datetime.now(UTC).isoformat(), "sources": {}}
    try:
        bus_mode = await bus.connect()
        results["bus"] = bus_mode
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for name in _pkg.SOURCE_POLLERS:
                stats = await _run_source(name, client, state, bus, ch_logger, now, dry_run)
                results["sources"][name] = stats
    finally:
        await bus.close()
    results["total_new"] = sum(s.get("new", 0) for s in results["sources"].values())
    results["total_detected"] = sum(s.get("detected", 0) for s in results["sources"].values())
    return results
