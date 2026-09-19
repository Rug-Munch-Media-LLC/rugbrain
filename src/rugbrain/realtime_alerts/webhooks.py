"""Community webhook registry and signed delivery."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import WEBHOOK_REDIS_PREFIX
from .models import CriticalAlert
from .payloads import build_webhook_payload
from .signing import sign_payload

log = logging.getLogger("rugbrain.realtime_alerts")


class WebhookRegistry:
    """Community webhook subscriptions (Redis-backed, in-memory fallback).

    Every registration gets a generated ``webhook_id`` and an HMAC secret
    (returned exactly once). Deliveries are signed so receivers can verify
    the payload really came from RugBrain.
    """

    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}
        self._redis: Any = None

    async def _client(self) -> Any | None:
        from rugbrain import realtime_alerts as _pkg

        if self._redis is None:
            self._redis = await _pkg._get_redis()
        return self._redis

    async def register(
        self,
        url: str,
        name: str = "",
        secret: str = "",
    ) -> dict[str, Any]:
        webhook_id = uuid.uuid4().hex[:16]
        secret = secret or uuid.uuid4().hex
        record = {
            "id": webhook_id,
            "url": url,
            "name": name or "",
            "secret": secret,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._memory[webhook_id] = record
        r = await self._client()
        if r is not None:
            try:
                await r.set(
                    f"{WEBHOOK_REDIS_PREFIX}{webhook_id}",
                    json.dumps(record),
                    ex=60 * 60 * 24 * 365,
                )
            except Exception as e:
                log.warning("webhook_redis_store_failed: %s", e)
        log.info("webhook_registered id=%s url=%s", webhook_id, url)
        return {"webhook_id": webhook_id, "secret": secret, "url": url, "name": record["name"]}

    async def unregister(self, webhook_id: str) -> bool:
        removed = self._memory.pop(webhook_id, None)
        r = await self._client()
        if r is not None:
            try:
                key = f"{WEBHOOK_REDIS_PREFIX}{webhook_id}"
                if await r.delete(key):
                    removed = removed or {}
            except Exception as e:
                log.warning("webhook_redis_delete_failed: %s", e)
        return removed is not None

    async def list(self) -> list[dict[str, Any]]:
        r = await self._client()
        if r is not None:
            try:
                keys = await r.keys(f"{WEBHOOK_REDIS_PREFIX}*")
                records = []
                for key in keys:
                    data = await r.get(key)
                    if data:
                        records.append(json.loads(data))
                if records:
                    # Redis is authoritative when present; merge in-memory
                    # records that have not been persisted yet.
                    seen = {rec["id"] for rec in records}
                    for rec in self._memory.values():
                        if rec["id"] not in seen:
                            records.append(rec)
                    return records
            except Exception as e:
                log.warning("webhook_redis_list_failed: %s", e)
        return list(self._memory.values())

    async def clear(self) -> None:
        """Test/dev helper — drop every registration."""
        self._memory.clear()
        r = await self._client()
        if r is not None:
            try:
                keys = await r.keys(f"{WEBHOOK_REDIS_PREFIX}*")
                if keys:
                    await r.delete(*keys)
            except Exception as e:
                log.warning("webhook_redis_clear_failed: %s", e)


async def deliver_webhooks(
    alert: CriticalAlert,
    registry: WebhookRegistry | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST the alert to every registered webhook, HMAC-signed.

    Returns ``{"delivered": n, "failed": n, "errors": [url,...]}``.
    """
    registry = registry or _webhook_registry
    hooks = await registry.list()
    payload = build_webhook_payload(alert)
    body = json.dumps(payload, default=str)
    delivered = 0
    errors: list[str] = []
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        for hook in hooks:
            url = str(hook.get("url", ""))
            secret = str(hook.get("secret", ""))
            if not url:
                continue
            try:
                resp = await http.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": sign_payload(secret, body),
                        "X-Webhook-Id": str(hook.get("id", "")),
                        "X-Webhook-Event": "critical_alert",
                        "User-Agent": "RugBrain-Realtime-Alerts/1.0",
                    },
                )
                if resp.status_code < 300:
                    delivered += 1
                else:
                    errors.append(f"{url} (HTTP {resp.status_code})")
            except Exception as e:
                errors.append(f"{url} ({str(e)[:80]})")
    finally:
        if own_client:
            await http.aclose()
    if errors:
        log.warning("webhook_delivery_partial delivered=%d failed=%d", delivered, len(errors))
    return {"delivered": delivered, "failed": len(errors), "errors": errors}


_webhook_registry = WebhookRegistry()


def get_webhook_registry() -> WebhookRegistry:
    """Return the process-wide webhook registry (shared with the API)."""
    return _webhook_registry
