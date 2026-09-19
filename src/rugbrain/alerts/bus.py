"""Message bus — NATS primary, Redis pub/sub fallback."""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from . import config
from .models import SecurityAlert

log = logging.getLogger("rugbrain.alerts")


class AlertBus:
    """Publish alerts to NATS ``alerts.crypto.security`` (or Redis pub/sub).

    The subject name is identical in both transports so a downstream
    consumer can subscribe to either without knowing which bus is active.
    """

    def __init__(self, subject: str | None = None) -> None:
        self._nats: Any = None
        self._nc: Any = None
        self._redis: Any = None
        self._mode = "none"
        # Allow callers (e.g. rugbrain.realtime_alerts) to publish on a
        # different subject while reusing the same transport fallback.
        self._subject = subject or config.NATS_SUBJECT

    async def connect(self) -> str:
        """Establish the bus connection. Returns the active transport name."""
        if config.NATS_URL:
            nats_mod = self._load_nats_module()
            if nats_mod is not None:
                try:
                    self._nc = await nats_mod.connect(config.NATS_URL, max_reconnect_attempts=3)
                    self._mode = "nats"
                    log.info("alert_bus_connected transport=nats url=%s", config.NATS_URL)
                    return self._mode
                except Exception as e:
                    log.warning("alert_bus_nats_connect_failed: %s", e)
            else:
                log.warning("alert_bus_nats_disabled reason=package_missing (install nats-py)")
        # Redis fallback
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
            self._mode = "redis"
            log.info("alert_bus_connected transport=redis subject=%s", self._subject)
        except Exception as e:
            log.warning("alert_bus_redis_failed: %s", e)
            self._mode = "none"
        return self._mode

    @staticmethod
    def _load_nats_module() -> Any | None:
        try:
            return importlib.import_module("nats")
        except Exception:
            return None

    async def publish(self, alert: SecurityAlert) -> bool:
        return await self.publish_payload(alert.model_dump())

    async def publish_payload(self, payload: dict[str, Any]) -> bool:
        """Publish an arbitrary JSON-serializable payload on the subject."""
        message = json.dumps(payload, default=str)
        if self._mode == "nats" and self._nc is not None:
            try:
                await self._nc.publish(self._subject, message.encode("utf-8"))
                return True
            except Exception as e:
                log.warning("alert_bus_nats_publish_failed: %s", e)
                return False
        if self._mode == "redis" and self._redis is not None:
            try:
                await self._redis.publish(self._subject, message)
                return True
            except Exception as e:
                log.warning("alert_bus_redis_publish_failed: %s", e)
                return False
        log.warning("alert_bus_not_connected drop=1 subject=%s", self._subject)
        return False

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception as e:
                log.debug("alert_bus_nats_close_failed: %s", e)
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as e:
                log.debug("alert_bus_redis_close_failed: %s", e)
