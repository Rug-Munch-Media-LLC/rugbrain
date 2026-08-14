"""FastAPI routes for real-time exploit alerts + community webhooks.

Surface (all under ``/api/v1/alerts``):

  GET  /live                   — current active threats
  GET  /recent?minutes=60      — alerts from the last N minutes
  POST /webhook/register       — subscribe a community webhook (HMAC secret)
  POST /webhook/unregister     — remove a webhook by id
  POST /webhook/test           — send a signed test ping to a webhook

Webhook payloads are HMAC-SHA256 signed (``X-Webhook-Signature: sha256=<hex>``
over the raw body) so receivers can verify the source with
``rugbrain.realtime_alerts.verify_signature`` or any HMAC-SHA256 tool.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from rugbrain.realtime_alerts import (
    _store,
    build_webhook_payload,
    get_webhook_registry,
    scroll_live_threats,
    sign_payload,
)

log = logging.getLogger("rugbrain.alert_routes")

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


class WebhookRegisterRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    name: str = Field(default="", max_length=128)
    secret: str = Field(default="", max_length=128)


class WebhookUnregisterRequest(BaseModel):
    webhook_id: str = Field(..., min_length=1, max_length=64)


class WebhookTestRequest(BaseModel):
    webhook_id: str = Field(..., min_length=1, max_length=64)


def _validate_webhook_url(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    if "\n" in url or "\r" in url:
        raise HTTPException(status_code=400, detail="url contains invalid characters")


@router.get("/live")
async def alerts_live() -> dict[str, Any]:
    """Current active threats (Qdrant ``live_threats`` first, in-memory fallback)."""
    qdrant_items: list[dict[str, Any]] = []
    try:
        qdrant_items = await scroll_live_threats()
    except Exception as e:
        log.debug("live_scroll_failed: %s", e)
    if qdrant_items:
        return {
            "status": "ok",
            "source": "qdrant",
            "count": len(qdrant_items),
            "threats": qdrant_items,
        }
    items = _store.live_threats()
    return {"status": "ok", "source": "memory", "count": len(items), "threats": items}


@router.get("/recent")
async def alerts_recent(
    minutes: int = Query(default=60, ge=1, le=1440),
) -> dict[str, Any]:
    """Alerts detected in the last *minutes* (1 min .. 24 h)."""
    items = _store.recent_alerts(minutes)
    return {"status": "ok", "minutes": minutes, "count": len(items), "alerts": items}


@router.post("/webhook/register")
async def webhook_register(req: WebhookRegisterRequest) -> dict[str, Any]:
    """Register a community webhook to receive critical alerts.

    Returns ``webhook_id`` and ``secret`` — the secret is shown exactly
    once and is used to verify ``X-Webhook-Signature`` on every delivery.
    """
    _validate_webhook_url(req.url)
    registry = get_webhook_registry()
    result = await registry.register(url=req.url, name=req.name, secret=req.secret)
    log.info("webhook_registered id=%s", result["webhook_id"])
    return {
        "status": "ok",
        "webhook_id": result["webhook_id"],
        "url": result["url"],
        "name": result["name"],
        "secret": result["secret"],
        "note": "Save this secret — it is only returned once and signs every delivery (X-Webhook-Signature: sha256=<hex> of the raw body).",
    }


@router.post("/webhook/unregister")
async def webhook_unregister(req: WebhookUnregisterRequest) -> dict[str, Any]:
    """Remove a webhook by its id."""
    registry = get_webhook_registry()
    ok = await registry.unregister(req.webhook_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"webhook {req.webhook_id} not found")
    log.info("webhook_unregistered id=%s", req.webhook_id)
    return {"status": "ok", "webhook_id": req.webhook_id}


@router.post("/webhook/test")
async def webhook_test(req: WebhookTestRequest) -> dict[str, Any]:
    """Send a signed test ping to a registered webhook (verification aid)."""
    from rugbrain.realtime_alerts import CriticalAlert

    registry = get_webhook_registry()
    hooks = await registry.list()
    hook = next((h for h in hooks if h.get("id") == req.webhook_id), None)
    if not hook:
        raise HTTPException(status_code=404, detail=f"webhook {req.webhook_id} not found")
    alert = CriticalAlert(
        title="RugBrain webhook test",
        description="This is a signed test ping — no exploit detected.",
        source="test",
        chain="test",
        severity="info",
    )
    payload = build_webhook_payload(alert)
    body = json.dumps(payload, default=str)
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                str(hook["url"]),
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": sign_payload(str(hook.get("secret", "")), body),
                    "X-Webhook-Id": str(hook.get("id", "")),
                    "X-Webhook-Event": "test_ping",
                    "User-Agent": "RugBrain-Realtime-Alerts/1.0",
                },
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"test ping failed: {e}") from e
    return {
        "status": "ok",
        "webhook_id": req.webhook_id,
        "http_status": resp.status_code,
        "delivered": resp.status_code < 300,
    }


__all__ = ["router"]
