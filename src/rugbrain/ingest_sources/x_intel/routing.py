"""Lane routing for X/Twitter intel — GetXAPI (metered) vs Pry (free).

Ported from rugmunchbot ``getxapi_budget.should_use_getxapi`` +
``x_ingest.route_account_to_source``.

Rules:
* TIER 1/2 accounts → GetXAPI (paid, reliable). TIER 3/4 long tail → Pry.
* ``search`` / ``user-info`` / ``trends`` / ``user-status`` → GetXAPI.
* ``user-mentions`` → Pry (free to scrape).
* ``user-tweets`` → GetXAPI only for high-value accounts (>50k followers);
  unknown → conservative GetXAPI.
* Pacing override: while pacing is active every endpoint goes Pry-only.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rugbrain.ingest_sources.x_intel.config import (
    HIGH_VALUE_FOLLOWERS,
    TIER1_ACCOUNTS,
    TIER2_ACCOUNTS,
)

logger = logging.getLogger(__name__)

_GETXAPI_ENDPOINTS = frozenset({"search", "advanced-search", "user-info", "trends", "user-status"})
_PRY_ENDPOINTS = frozenset({"user-mentions"})


async def pacing_active(redis_client: Any | None = None) -> bool:
    """True while the Pry-only pacing lock is held (fail-open False)."""
    try:
        if redis_client is not None:
            raw = await redis_client.get("xintel:pacing_until")
            return float(raw or 0.0) > time.time()
        from rugbrain.compat import get_redis_async

        r = await get_redis_async()
        raw = await r.get("xintel:pacing_until")
        return float(raw or 0.0) > time.time()
    except Exception as exc:
        logger.debug("xintel_routing:pacing_check_failed err=%s", str(exc)[:120])
        return False


def route_account(handle: str) -> str:
    """Route one monitored account: TIER 1/2 → ``getxapi``, else ``pry``."""
    h = str(handle or "").strip().lstrip("@")
    if h in TIER1_ACCOUNTS or h in TIER2_ACCOUNTS:
        return "getxapi"
    return "pry"


async def should_use_getxapi(
    endpoint: str,
    params: dict[str, Any] | None = None,
    redis_client: Any | None = None,
) -> bool:
    """Decide per-request: True → metered GetXAPI, False → free Pry lane.

    ``params`` may carry ``followers_count`` for ``user-tweets`` value
    classification. Fail-open: Redis down → pacing can't engage, routing is
    purely rule-based.
    """
    if await pacing_active(redis_client):
        logger.debug("xintel_routing:pacing endpoint=%s → pry", endpoint)
        return False
    ep = str(endpoint or "").strip().lower().replace("/", "-")
    if ep in _PRY_ENDPOINTS:
        return False
    if ep in _GETXAPI_ENDPOINTS:
        return True
    if ep in ("user-tweets", "user-timeline"):
        followers = (params or {}).get("followers_count")
        if followers is None:
            return True  # unknown → conservative GetXAPI
        try:
            return int(followers) > HIGH_VALUE_FOLLOWERS
        except (TypeError, ValueError):
            return True
    logger.debug("xintel_routing:unknown endpoint=%s → getxapi", endpoint)
    return True


__all__ = ["pacing_active", "route_account", "should_use_getxapi"]
