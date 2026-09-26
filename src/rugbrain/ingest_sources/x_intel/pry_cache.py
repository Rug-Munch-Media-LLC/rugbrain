"""Redis cache primitives for the Pry scrape lane.

A cache hit costs ZERO Pry calls, so both scrape helpers read before they
write. All cache faults are fail-open: a Redis outage degrades to an extra
scrape, never to a failed cycle.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def cache_get(key: str) -> Any | None:
    """Return the cached JSON value for *key*, or None on miss/fault."""
    try:
        from rugbrain.compat import get_redis_async

        raw = await (await get_redis_async()).get(key)
        return json.loads(raw) if raw is not None else None
    except Exception:
        logger.debug("xintel:pry_cache_read_failed key=%s", key)
        return None


async def cache_set(key: str, value: Any, ttl: float) -> None:
    """Store *value* as JSON under *key* for *ttl* seconds (fail-open)."""
    try:
        from rugbrain.compat import get_redis_async

        await (await get_redis_async()).set(key, json.dumps(value), ex=int(ttl))
    except Exception:
        logger.debug("xintel:pry_cache_write_failed key=%s", key)


__all__ = ["cache_get", "cache_set"]
