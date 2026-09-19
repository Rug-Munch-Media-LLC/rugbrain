"""Process-local live-threat store + Redis-backed dedup/cooldown."""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import (
    LIVE_TTL_SECONDS,
    MAX_RECENT,
    RECENT_REDIS_KEY,
    SEEN_REDIS_PREFIX,
    SEEN_TTL,
)
from .models import CriticalAlert
from .payloads import build_webhook_payload

log = logging.getLogger("rugbrain.realtime_alerts")


class LiveThreatStore:
    """Process-local view of active + recent alerts.

    Redis is used for cross-process dedup and a durable recent-alert list;
    the in-memory structures guarantee the API answers even with Redis
    down.
    """

    def __init__(self) -> None:
        self._live: dict[str, dict[str, Any]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT)
        self._seen: set[str] = set()
        self._redis: Any = None

    async def _client(self) -> Any | None:
        from rugbrain import realtime_alerts as _pkg

        if self._redis is None:
            self._redis = await _pkg._get_redis()
        return self._redis

    async def is_seen(self, alert_id: str) -> bool:
        if alert_id in self._seen:
            return True
        r = await self._client()
        if r is not None:
            try:
                key = f"{SEEN_REDIS_PREFIX}{alert_id}"
                if await r.get(key):
                    return True
                await r.set(key, "1", ex=SEEN_TTL)
            except Exception as e:
                log.debug("seen_redis_failed: %s", e)
        self._seen.add(alert_id)
        return False

    async def record(self, alert: CriticalAlert) -> bool:
        """Record an alert (dedup check inside). Returns True if new."""
        if await self.is_seen(alert.id):
            return False
        item = build_webhook_payload(alert)
        item["status"] = alert.status
        item["_expires_at"] = time.time() + LIVE_TTL_SECONDS
        self._live[alert.id] = item
        self._recent.appendleft(item)
        r = await self._client()
        if r is not None:
            try:
                await r.lpush(RECENT_REDIS_KEY, json.dumps(item, default=str))
                await r.ltrim(RECENT_REDIS_KEY, 0, MAX_RECENT - 1)
            except Exception as e:
                log.debug("recent_redis_push_failed: %s", e)
        return True

    def live_threats(self) -> list[dict[str, Any]]:
        now = time.time()
        stale = [aid for aid, item in self._live.items() if item.get("_expires_at", 0) < now]
        for aid in stale:
            self._live.pop(aid, None)
        items = [dict(item) for item in self._live.values()]
        items.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
        for item in items:
            item.pop("_expires_at", None)
        return items

    def recent_alerts(self, minutes: int) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
        items = [dict(item) for item in self._recent]
        items.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
        return [item for item in items if item.get("detected_at", "") >= cutoff]

    def clear(self) -> None:
        """Test/dev helper — drop process-local state (Redis dedup stays)."""
        self._live.clear()
        self._recent.clear()
        self._seen.clear()


_store = LiveThreatStore()


def get_live_threats() -> list[dict[str, Any]]:
    """API helper: current active threats (process-local store)."""
    return _store.live_threats()


def get_recent_alerts(minutes: int = 60) -> list[dict[str, Any]]:
    """API helper: alerts detected in the last *minutes* (process-local store)."""
    return _store.recent_alerts(minutes)
