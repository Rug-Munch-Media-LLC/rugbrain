"""Lazy async Redis client helper (None when unavailable)."""

from __future__ import annotations

import logging
from typing import Any

from .config import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

log = logging.getLogger("rugbrain.realtime_alerts")


async def _get_redis() -> Any | None:
    """Lazy async Redis client (None when unavailable)."""
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        await r.ping()
        return r
    except Exception as e:
        log.debug("redis_unavailable: %s", e)
        return None
