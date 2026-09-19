"""Redis-backed embedding cache."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, cast

from .config import REDIS_HOST, REDIS_PORT, REDIS_PW

logger = logging.getLogger(__name__)


class CacheMixin:
    """Redis connection + embedding cache helpers."""

    _redis: Any
    _cache_hits: int
    _cache_misses: int

    async def _get_redis(self) -> Any:
        import redis.asyncio as redis

        if self._redis is None:
            self._redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PW or None,
                db=0,
                decode_responses=True,
            )
        return self._redis

    async def _cache_key(self, prefix: str, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()[:32]
        return f"emb:{prefix}:{h}"

    async def _cache_get(self, key: str) -> list[float] | None:
        try:
            r = await self._get_redis()
            data = await r.get(key)
            if data:
                self._cache_hits += 1
                return cast(list[float], json.loads(data))
            self._cache_misses += 1
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
        return None

    async def _cache_set(self, key: str, vector: list[float], ttl: int = 86400) -> None:
        try:
            r = await self._get_redis()
            await r.setex(key, ttl, json.dumps(vector))
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
