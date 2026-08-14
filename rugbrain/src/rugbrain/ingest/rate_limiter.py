"""RateLimiter for the RAG ingestion pipeline — prevents embedding backend overload."""

from __future__ import annotations

import time

from rugbrain._logging import get_logger
from rugbrain.core.config import RAG_INGESTION_MAX_PER_MINUTE

log = get_logger(__name__)

_INGESTION_COUNTER_KEY = "rag:rate:ingestions"


class RateLimiter:
    """Token-bucket rate limiter using Redis.

    Prevents the embedding backend (Infinity / OpenRouter) from being
    overwhelmed during bulk backfills.  Tracks ingestions per minute.
    """

    def __init__(self, max_per_minute: int = RAG_INGESTION_MAX_PER_MINUTE) -> None:
        self.max_per_minute = max_per_minute

    async def check(self, count: int = 1) -> bool:
        """Can ``count`` ingestions proceed without exceeding the rate limit?"""
        try:
            from rugbrain.storage.cache import get_cache

            r = get_cache()
            if r is None:
                return True
            now = int(time.time())
            window_start = now - 60

            r.zremrangebyscore(_INGESTION_COUNTER_KEY, 0, window_start)
            current = r.zcard(_INGESTION_COUNTER_KEY) or 0
            remaining = self.max_per_minute - current

            if remaining < count:
                log.warning("rate_limiter_blocked", current=current, max=self.max_per_minute)
                return False

            pipe = r.pipeline()
            for _ in range(count):
                pipe.zadd(_INGESTION_COUNTER_KEY, {f"{now}:{id(self)}:{hash(time.time())}": now})
            pipe.expire(_INGESTION_COUNTER_KEY, 120)
            pipe.execute()
            return True
        except Exception as e:
            log.warning("rate_limiter_check_failed: %s", e)
            return True

    async def remaining(self) -> int:
        """How many more ingestions are allowed this minute?"""
        try:
            from rugbrain.storage.cache import get_cache

            r = get_cache()
            if r is None:
                return self.max_per_minute
            now = int(time.time())
            r.zremrangebyscore(_INGESTION_COUNTER_KEY, 0, now - 60)
            current = r.zcard(_INGESTION_COUNTER_KEY) or 0
            return max(0, self.max_per_minute - current)
        except Exception as e:
            log.debug("rate_limiter_remaining_failed: %s", e)
            return self.max_per_minute

    async def reset(self) -> None:
        """Reset the rate limit counter (for testing)."""
        try:
            from rugbrain.storage.cache import get_cache

            r = get_cache()
            if r is None:
                return
            r.delete(_INGESTION_COUNTER_KEY)
        except Exception as e:
            log.debug("rate_limiter_reset_failed: %s", e)
