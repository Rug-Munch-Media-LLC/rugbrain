"""``/health`` and ``/ready`` — the liveness and readiness probes.

One job: assemble the health payload from live probes plus the boot flags.
Read-only with respect to boot state; it may refresh connectivity flags but
never starts or stops anything.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from rugbrain.app import probes
from rugbrain.app.health_state import STATE

logger = logging.getLogger(__name__)

router = APIRouter()


async def qdrant_schema_ok() -> bool:
    """Verify the live canonical collections have the production vector dimension."""
    return await probes.probe_qdrant_schema(STATE["qdrant_url"])


async def _managed_status(module: str, running_sym: str, stats_sym: str | None) -> tuple[Any, Any]:
    """Return ``(is_running, stats)`` for a managed subsystem, or ``(False, {})``."""
    try:
        mod = __import__(module, fromlist=[running_sym])
        running = bool(getattr(mod, running_sym)())
        stats = getattr(mod, stats_sym)() if stats_sym else {}
        return running, stats
    except Exception:
        return False, {}


async def health() -> dict[str, Any]:
    """Liveness probe — checks Qdrant + Redis connectivity and subsystem state."""
    qdrant_ok = await probes.probe_qdrant(STATE["qdrant_url"], timeout=3.0)
    schema_ok = await qdrant_schema_ok()
    STATE["qdrant_schema"] = schema_ok
    redis_ok = await probes.probe_redis()
    queue_depth = await probes.redis_queue_depth()

    sub_running, sub_stats = await _managed_status(
        "rugbrain.scanner_subscriber", "is_subscriber_running", "subscriber_stats"
    )
    poll_running, poll_stats = await _managed_status(
        "rugbrain.scanner_poller", "is_poller_running", "poller_stats"
    )
    fear_running, _ = await _managed_status(
        "rugbrain.fear_index", "is_fear_index_poller_running", None
    )
    realtime_running, realtime_stats = await _managed_status(
        "rugbrain.realtime_alerts", "is_realtime_monitor_running", "realtime_monitor_stats"
    )
    news_running, news_stats = await _managed_status(
        "rugbrain.news_realtime", "is_news_story_updater_running", "news_story_updater_stats"
    )

    status = "ok" if qdrant_ok and schema_ok and redis_ok else "degraded"
    STATE["status"] = status
    payload: dict[str, Any] = {
        "status": status,
        "qdrant": qdrant_ok,
        "qdrant_schema": schema_ok,
        "redis": redis_ok,
        "qdrant_url": STATE["qdrant_url"],
        "redis_host": STATE["redis_host"],
        "queue_depth": queue_depth,
        "scanner_subscriber": sub_running,
        "subscriber_stats": sub_stats,
        "scanner_poller": poll_running,
        "poller_stats": poll_stats,
        "fear_index_poller": fear_running,
        "realtime_monitor": realtime_running,
        "realtime_monitor_stats": realtime_stats,
        "news_story_updater": news_running,
        "news_story_updater_stats": news_stats,
    }
    from rugbrain.app.boot_steps import BARE_FLAG_NAMES

    for name in BARE_FLAG_NAMES:
        payload[name] = STATE.get(name, False)
    payload["qdrant_collections_auto"] = schema_ok
    return payload


@router.get("/health", tags=["system"])
async def health_route() -> dict[str, Any]:
    """GET /health — liveness probe."""
    return await health()


@router.get("/ready", tags=["system"])
async def ready() -> dict[str, str]:
    """Readiness probe — require live Qdrant, schema, and Redis health."""
    current = await health()
    return {
        "status": "ready" if current["status"] == "ok" else "degraded",
        "app": "rugbrain",
    }
