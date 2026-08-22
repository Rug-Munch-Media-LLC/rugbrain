"""Tests for rugbrain.alert_routes — /api/v1/alerts/* endpoints.

Uses httpx ASGITransport against a minimal FastAPI app (no auth
middleware), matching the existing endpoints.py test pattern.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import rugbrain.realtime_alerts as ra
from rugbrain.alert_routes import router


@pytest.fixture
def client() -> Any:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _no_redis() -> None:
    return None


@pytest.fixture
def clean_store(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Hermetic store: no Redis, no Qdrant — everything process-local."""
    import rugbrain.alert_routes as ar

    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    # Qdrant scroll is patched at the router level so /live falls back to
    # the process-local store instead of hitting a real Qdrant.
    async def no_scroll(limit: int = 100) -> Any:
        return []

    monkeypatch.setattr(ar, "scroll_live_threats", no_scroll)
    ra._store.clear()
    yield
    ra._store.clear()


@pytest.mark.asyncio
async def test_alerts_live_empty(client: Any, clean_store: Any) -> None:
    resp = await client.get("/api/v1/alerts/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["threats"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_alerts_live_reflects_store(client: Any, clean_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    await ra._store.record(
        ra.CriticalAlert(title="Live hack", source="test", chain="ethereum", amount_usd=5e6)
    )
    resp = await client.get("/api/v1/alerts/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["threats"][0]["title"] == "Live hack"


@pytest.mark.asyncio
async def test_alerts_recent_minutes_param(client: Any, clean_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ra, "_get_redis", _no_redis)
    from datetime import UTC, datetime, timedelta

    await ra._store.record(
        ra.CriticalAlert(
            title="Fresh",
            source="test",
            detected_at=datetime.now(UTC).isoformat(),
        )
    )
    await ra._store.record(
        ra.CriticalAlert(
            title="Stale",
            source="test",
            detected_at=(datetime.now(UTC) - timedelta(hours=3)).isoformat(),
        )
    )
    resp = await client.get("/api/v1/alerts/recent?minutes=60")
    assert resp.status_code == 200
    data = resp.json()
    titles = [a["title"] for a in data["alerts"]]
    assert "Fresh" in titles
    assert "Stale" not in titles


@pytest.mark.asyncio
async def test_alerts_recent_validation(client: Any) -> None:
    resp = await client.get("/api/v1/alerts/recent?minutes=99999")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_webhook_register_returns_secret_once(client: Any, clean_store: Any) -> None:
    resp = await client.post(
        "/api/v1/alerts/webhook/register",
        json={"url": "https://example.com/hook", "name": "community"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["webhook_id"]
    assert data["secret"]
    assert data["url"] == "https://example.com/hook"


@pytest.mark.asyncio
async def test_webhook_register_rejects_bad_url(client: Any) -> None:
    resp = await client.post(
        "/api/v1/alerts/webhook/register",
        json={"url": "ftp://bad.example/hook"},
    )
    assert resp.status_code == 400
    resp2 = await client.post(
        "/api/v1/alerts/webhook/register",
        json={"url": "javascript:alert(1)"},
    )
    assert resp2.status_code == 400


@pytest.mark.asyncio
async def test_webhook_register_custom_secret(client: Any, clean_store: Any) -> None:
    resp = await client.post(
        "/api/v1/alerts/webhook/register",
        json={"url": "https://example.com/hook", "secret": "my-secret-abc"},
    )
    assert resp.status_code == 200
    assert resp.json()["secret"] == "my-secret-abc"  # noqa: S105 — test fixture


@pytest.mark.asyncio
async def test_webhook_unregister(client: Any, clean_store: Any) -> None:
    reg = ra.get_webhook_registry()
    rec = await reg.register("https://example.com/hook")
    resp = await client.post(
        "/api/v1/alerts/webhook/unregister",
        json={"webhook_id": rec["webhook_id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    # Second removal → 404.
    resp2 = await client.post(
        "/api/v1/alerts/webhook/unregister",
        json={"webhook_id": rec["webhook_id"]},
    )
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_webhook_test_ping_signed(client: Any, clean_store: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    reg = ra.get_webhook_registry()
    rec = await reg.register("https://example.com/hook")
    captured: dict[str, Any] = {}

    async def fake_post(
            url: Any,
            content: Any = None,
            headers: Any = None,
            **kwargs: Any,
        ) -> Any:
        captured["content"] = content
        captured["headers"] = headers
        return httpx.Response(200)

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *a: Any) -> Any:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            return await fake_post(*args, **kwargs)

    monkeypatch.setattr(ra.httpx, "AsyncClient", FakeClient)

    resp = await client.post(
        "/api/v1/alerts/webhook/test",
        json={"webhook_id": rec["webhook_id"]},
    )
    assert resp.status_code == 200
    body = captured["content"].encode()
    sig = captured["headers"]["X-Webhook-Signature"]
    assert ra.verify_signature(rec["secret"], sig, body)
    assert captured["headers"]["X-Webhook-Event"] == "test_ping"


@pytest.mark.asyncio
async def test_webhook_test_404(client: Any) -> None:
    resp = await client.post(
        "/api/v1/alerts/webhook/test",
        json={"webhook_id": "does-not-exist"},
    )
    assert resp.status_code == 404
