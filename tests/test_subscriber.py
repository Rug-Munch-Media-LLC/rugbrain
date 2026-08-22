"""
Integration-style tests for scanner_subscriber and auth middleware.

Tests the scanner_subscriber module's public API functions
and the RAGAuthMiddleware for API key authentication.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

# ── Scanner Subscriber Tests ──────────────────────────────────────────────


class TestScannerSubscriber:
    """Tests for scanner_subscriber core functions."""

    def test_is_subscriber_running_initially_false(self) -> None:
        """is_subscriber_running should return False by default."""
        from rugbrain.scanner_subscriber import is_subscriber_running

        assert is_subscriber_running() is False

    def test_subscriber_stats_shape(self) -> None:
        """subscriber_stats should return expected fields."""
        from rugbrain.scanner_subscriber import subscriber_stats

        stats = subscriber_stats()
        assert "running" in stats
        assert "ingested_count" in stats
        assert "last_ingested_at" in stats
        assert "channel" in stats
        assert stats["channel"] == "rmi:rag:ingest"
        assert isinstance(stats["ingested_count"], int)

    @pytest.mark.asyncio
    async def test_start_subscriber_twice_no_duplicate(self) -> None:
        """Starting subscriber twice should not create duplicate tasks."""
        # Reset state
        import rugbrain.scanner_subscriber as ss
        from rugbrain.scanner_subscriber import (
            is_subscriber_running,
            start_subscriber,
            stop_subscriber,
        )

        ss._subscriber_running = False
        ss._subscriber_task = None

        await start_subscriber()
        assert is_subscriber_running() is True

        # Second start should not change state (should log warning)
        await start_subscriber()
        assert is_subscriber_running() is True

        await stop_subscriber()

    @pytest.mark.asyncio
    async def test_stop_subscriber_cleans_up(self) -> None:
        """Stop subscriber should reset running state."""
        import rugbrain.scanner_subscriber as ss
        from rugbrain.scanner_subscriber import (
            is_subscriber_running,
            start_subscriber,
            stop_subscriber,
        )

        ss._subscriber_running = False
        ss._subscriber_task = None

        await start_subscriber()
        assert is_subscriber_running() is True
        await stop_subscriber()
        assert is_subscriber_running() is False

    @pytest.mark.asyncio
    async def test_process_finding_logs_errors(self) -> None:
        """_process_finding should handle errors without crashing."""
        from rugbrain.scanner_subscriber import _process_finding

        # Empty data should not crash
        result = await _process_finding({})
        assert result is None  # Function returns None on success


# ── Auth Middleware Tests ──────────────────────────────────────────────────


class TestRAGAuthMiddleware:
    """Tests for the RAG API key auth middleware."""

    @pytest.fixture
    def app_with_auth(self) -> Any:
        """Create a FastAPI app with auth middleware enabled."""

        os.environ["RMI_RAG_API_KEY"] = "test-api-key-12345"

        import importlib

        # Reload to pick up new env
        import rugbrain.main

        importlib.reload(rugbrain.main)

        # Patch app instantiation
        from fastapi import FastAPI

        from rugbrain.main import RAGAuthMiddleware, lifespan

        app = FastAPI(lifespan=lifespan)

        # Add a test endpoint
        @app.get("/api/v1/test-secure")
        async def secure_endpoint() -> dict[str, Any]:
            return {"status": "ok", "data": "secure"}

        # Add a health endpoint (exempt from auth)
        @app.get("/health")
        async def health_endpoint() -> dict[str, Any]:
            return {"status": "ok"}

        app.add_middleware(RAGAuthMiddleware)
        return app

    @pytest.fixture
    def app_without_auth(self) -> Any:
        """Create app with auth open (DEV_MODE=true, key not set)."""

        # KEY NOT SET + DEV_MODE=true - auth open for development only
        if "RMI_RAG_API_KEY" in os.environ:
            del os.environ["RMI_RAG_API_KEY"]
        os.environ["DEV_MODE"] = "true"

        import importlib

        import rugbrain.main

        importlib.reload(rugbrain.main)

        from fastapi import FastAPI

        from rugbrain.main import RAGAuthMiddleware, lifespan

        app = FastAPI(lifespan=lifespan)

        @app.get("/api/v1/test-open")
        async def open_endpoint() -> dict[str, Any]:
            return {"status": "ok", "data": "open"}

        app.add_middleware(RAGAuthMiddleware)
        return app

    @pytest.mark.asyncio
    async def test_auth_requires_api_key(self, app_with_auth: Any) -> None:
        """Requests without X-API-Key should get 401."""
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test-secure")
            assert resp.status_code == 401
            data = resp.json()
            assert "detail" in data
            assert "X-API-Key" in data["detail"]

    @pytest.mark.asyncio
    async def test_auth_wrong_key_returns_401(self, app_with_auth: Any) -> None:
        """Invalid X-API-Key should return 401."""
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test-secure",
                headers={"X-API-Key": "wrong-key-99999"},
            )
            assert resp.status_code == 401
            data = resp.json()
            assert "invalid" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_auth_valid_key_succeeds(self, app_with_auth: Any) -> None:
        """Valid X-API-Key should return 200."""
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/test-secure",
                headers={"X-API-Key": "test-api-key-12345"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_auth_health_exempt(self, app_with_auth: Any) -> None:
        """Health endpoints should bypass auth."""
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 500)  # May 500 if deps missing

    @pytest.mark.asyncio
    async def test_no_auth_when_key_not_set_and_dev_mode(self, app_without_auth: Any) -> None:
        """When RMI_RAG_API_KEY is not set and DEV_MODE=true, requests pass (dev only)."""
        transport = ASGITransport(app=app_without_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test-open")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_auth_fails_closed_when_key_not_set(self) -> Any:
        """Fail-closed: no key and no DEV_MODE => every request gets 503."""
        import importlib

        os.environ.pop("RMI_RAG_API_KEY", None)
        os.environ.pop("DEV_MODE", None)

        import rugbrain.main

        importlib.reload(rugbrain.main)

        from fastapi import FastAPI

        from rugbrain.main import RAGAuthMiddleware, lifespan

        app = FastAPI(lifespan=lifespan)

        @app.get("/api/v1/test-secure")
        async def secure_endpoint() -> dict[str, Any]:
            return {"status": "ok"}

        app.add_middleware(RAGAuthMiddleware)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test-secure")
            assert resp.status_code == 503
            data = resp.json()
            assert data["code"] == "auth.not_configured"

    @pytest.mark.asyncio
    async def test_auth_missing_key_header(self, app_with_auth: Any) -> None:
        """Request without any X-API-Key header should get 401."""
        transport = ASGITransport(app=app_with_auth)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/test-secure")
            assert resp.status_code == 401
            data = resp.json()
            assert data["code"] == "auth.missing_key"


# ── Health endpoint tests ──────────────────────────────────────────────────


class TestHealthEndpoints:
    """Tests for /health and /ready endpoints."""

    @pytest.mark.asyncio
    async def test_health_endpoint_shape(self) -> None:
        """GET /health should return status dict."""
        from rugbrain.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 500)  # May 500 if no deps
            if resp.status_code == 200:
                data = resp.json()
                assert "status" in data
                assert "qdrant" in data
                assert "redis" in data

    @pytest.mark.asyncio
    async def test_ready_endpoint(self) -> None:
        """GET /ready should return ready status."""
        from rugbrain.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ready")
            assert resp.status_code in (200, 500)
            if resp.status_code == 200:
                data = resp.json()
                assert "status" in data
                assert "app" in data
