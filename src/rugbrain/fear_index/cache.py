"""Redis cache read/write for fear-index snapshots (fail-open)."""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import CACHE_KEY, CACHE_TTL, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

log = logging.getLogger("rugbrain.fear_index")


async def _redis_get() -> str | None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        try:
            value = await r.get(CACHE_KEY)
            return str(value) if value is not None else None
        finally:
            await r.aclose()
    except Exception:
        return None


async def _cache_get() -> dict[str, Any] | None:
    cached = await _redis_get()
    if not cached:
        return None
    try:
        loaded: dict[str, Any] = json.loads(cached)
        return loaded
    except json.JSONDecodeError:
        return None


async def _cache_set(snapshot: dict[str, Any]) -> None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        try:
            await r.set(CACHE_KEY, json.dumps(snapshot), ex=CACHE_TTL)
        finally:
            await r.aclose()
    except Exception as e:
        log.debug("fear_index_cache_write_failed: %s", e)
