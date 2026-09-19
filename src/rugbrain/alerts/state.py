"""Rate-limit + dedup state (Redis with in-memory fallback)."""

from __future__ import annotations

import logging
from typing import Any

from . import config

log = logging.getLogger("rugbrain.alerts")


class AlertState:
    """Rate-limit + dedup state.

    Uses Redis when reachable (survives restarts, shared across processes);
    falls back to an in-memory dict so the poller still works in tests /
    minimal environments.
    """

    LAST_POLL_PREFIX = "rag:alerts:last_poll:"
    SEEN_PREFIX = "rag:alerts:seen:"

    def __init__(self) -> None:
        self._redis: Any = None
        self._memory: dict[str, Any] = {}
        self._redis_ok = False

    async def _client(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                password=config.REDIS_PASSWORD,
                decode_responses=True,
            )
            await r.ping()
            self._redis = r
            self._redis_ok = True
        except Exception as e:
            log.debug("alert_state_redis_unavailable: %s", e)
            self._redis = False
        return self._redis

    async def last_poll(self, source: str) -> float | None:
        c = await self._client()
        if c:
            try:
                v = await c.get(f"{self.LAST_POLL_PREFIX}{source}")
                return float(v) if v else None
            except Exception:
                return None
        return self._memory.get(f"poll:{source}")

    async def set_last_poll(self, source: str, ts: float) -> None:
        c = await self._client()
        if c:
            try:
                await c.set(f"{self.LAST_POLL_PREFIX}{source}", str(ts))
            except Exception as e:
                log.debug("alert_state_set_poll_failed: %s", e)
        self._memory[f"poll:{source}"] = ts

    async def is_seen(self, alert_id: str) -> bool:
        c = await self._client()
        if c:
            try:
                return bool(await c.exists(f"{self.SEEN_PREFIX}{alert_id}"))
            except Exception as e:
                log.debug("alert_state_seen_check_failed: %s", e)
                return False
        return alert_id in self._memory.get("seen", set())

    async def mark_seen(self, alert_id: str) -> None:
        c = await self._client()
        if c:
            try:
                await c.set(f"{self.SEEN_PREFIX}{alert_id}", "1", ex=config.SEEN_TTL)
            except Exception as e:
                log.debug("alert_state_mark_seen_failed: %s", e)
        self._memory.setdefault("seen", set()).add(alert_id)
