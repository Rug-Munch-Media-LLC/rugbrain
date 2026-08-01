"""
RMI RAG — FastAPI application entry point.

Mounts the existing APIRouter from endpoints.py, adds health/readiness probes,
CORS middleware, API key authentication, and OpenTelemetry integration.

Usage:
    uvicorn rmi_rag.main:app --host 0.0.0.0 --port 8012
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Install optional ``app.*`` integration stubs FIRST — the standalone
# rmi-rag container never ships the rmi-backend ``app`` package, so
# ``from app.x import y`` must degrade to a stub instead of raising.
from rmi_rag.import_guard import install_import_guard

install_import_guard()

# Deliberately after install_import_guard() so app.* stubs exist first.
from rmi_rag.endpoints import router as rag_router  # noqa: E402
from rmi_rag.endpoints import scam_check_router  # noqa: E402

logger = logging.getLogger(__name__)

# ── Env ──────────────────────────────────────────────────────────────
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "").lower() in ("1", "true", "yes")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
RMI_RAG_API_KEY = os.getenv("RMI_RAG_API_KEY", "")
DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("1", "true", "yes")

if not RMI_RAG_API_KEY:
    if DEV_MODE:
        logger.warning(
            "DEV_MODE=true and RMI_RAG_API_KEY is not set — API endpoints are "
            "accessible WITHOUT authentication (development only)."
        )
    else:
        logger.error(
            "RMI_RAG_API_KEY is not set and DEV_MODE is false — the API will "
            "REFUSE ALL REQUESTS (503). Set RMI_RAG_API_KEY in production or "
            "DEV_MODE=true for local development."
        )

# ── Health probes (shared state) ─────────────────────────────────────
_health_state: dict[str, Any] = {
    "status": "starting",
    "qdrant": False,
    "redis": False,
    "qdrant_url": "",
    "redis_host": "",
    "scanner_subscriber": False,
    "subscriber_stats": {},
    "scanner_poller": False,
    "poller_stats": {},
}


# ── Auth Middleware ───────────────────────────────────────────────────
class RAGAuthMiddleware(BaseHTTPMiddleware):
    """API key authentication via X-API-Key header.

    Exempts /health and /ready endpoints.
    All other endpoints require a valid API key.

    Fail-closed: if ``RMI_RAG_API_KEY`` is unset the middleware refuses
    ALL requests (503) unless ``DEV_MODE=true`` is set for development.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        path = request.url.path

        # Exempt health/readiness probes
        if path in ("/health", "/ready") or path.startswith("/health"):
            return await call_next(request)

        # Fail closed: refuse all requests when no API key is configured
        # unless DEV_MODE=true explicitly opts in to open auth (development).
        if not RMI_RAG_API_KEY and not DEV_MODE:
            logger.error("auth_refused path=%s reason=key_not_configured", path)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Service unavailable — RMI_RAG_API_KEY not configured",
                    "code": "auth.not_configured",
                },
            )

        # DEV_MODE: API key not configured, auth open (development only)
        if not RMI_RAG_API_KEY:
            return await call_next(request)

        # Require X-API-Key header
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            logger.warning("auth_failed path=%s reason=missing_api_key", path)
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Unauthorized — X-API-Key header required",
                    "code": "auth.missing_key",
                },
            )

        if api_key != RMI_RAG_API_KEY:
            logger.warning("auth_failed path=%s reason=invalid_api_key", path)
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Unauthorized — invalid X-API-Key",
                    "code": "auth.invalid_key",
                },
            )

        return await call_next(request)


# ── Lifespan ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup: configure OTel, warm health state. Shutdown: flush, close."""
    # ── OTel ──────────────────────────────────────────────────────
    if OTEL_ENABLED and OTEL_EXPORTER_OTLP_ENDPOINT:
        try:
            from rmi_rag.observability import _langfuse  # noqa: F401

            logger.info(
                "OTel observability enabled exporter=%s",
                OTEL_EXPORTER_OTLP_ENDPOINT,
            )
        except Exception as exc:
            logger.warning("OTel setup skipped: %s", exc)

    # ── Warm health state ─────────────────────────────────────────
    _health_state["status"] = "starting"
    _health_state["qdrant_url"] = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    _health_state["redis_host"] = os.getenv("REDIS_HOST", "localhost")

    # Probe Qdrant once at startup
    try:
        async with httpx.AsyncClient(base_url=_health_state["qdrant_url"], timeout=5.0) as client:
            r = await client.get("/")
            _health_state["qdrant"] = r.status_code == 200
            logger.info("qdrant_probe status=%s", "ok" if _health_state["qdrant"] else "fail")
    except Exception as exc:
        logger.warning("qdrant_probe failed: %s", exc)
        _health_state["qdrant"] = False

    # Probe Redis once at startup
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=_health_state["redis_host"],
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        await r.ping()
        _health_state["redis"] = True
        logger.info("redis_probe status=ok")
        await r.close()
    except Exception as exc:
        logger.warning("redis_probe failed: %s", exc)
        _health_state["redis"] = False

    # ── Start scanner subscriber + poller (background RAG ingestion) ──
    # Primary: Redis pub/sub. Fallback: BLPop polling (for DragonflyDB pub/sub issues).

    # Start pub/sub subscriber
    _sub_ok = False
    try:
        from rmi_rag.scanner_subscriber import (
            is_subscriber_running,
            start_subscriber,
            subscriber_stats,
        )

        await start_subscriber()
        _sub_ok = is_subscriber_running()
        _health_state["scanner_subscriber"] = _sub_ok
        _health_state["subscriber_stats"] = subscriber_stats()
        logger.info(
            "scanner_subscriber status=%s",
            "ok" if _sub_ok else "fail",
        )
    except Exception as exc:
        logger.warning("scanner_subscriber_start_failed: %s", exc)
        _health_state["scanner_subscriber"] = False

    # Start polling fallback (always runs regardless of pub/sub state)
    try:
        from rmi_rag.scanner_poller import (
            is_poller_running,
            poller_stats,
            start_poller,
        )

        await start_poller()
        _poll_ok = is_poller_running()
        _health_state["scanner_poller"] = _poll_ok
        _health_state["poller_stats"] = poller_stats()
        logger.info(
            "scanner_poller status=%s",
            "ok" if _poll_ok else "fail",
        )
    except Exception as exc:
        logger.warning("scanner_poller_start_failed: %s", exc)
        _health_state["scanner_poller"] = False

    _health_state["status"] = "ok"
    logger.info(
        "rmi_rag_started qdrant=%s redis=%s subscriber=%s",
        _health_state["qdrant"],
        _health_state["redis"],
        _health_state["scanner_subscriber"],
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────────
    try:
        from rmi_rag.scanner_subscriber import stop_subscriber

        await stop_subscriber()
    except Exception:
        logger.debug("subscriber_stop skipped")

    try:
        from rmi_rag.scanner_poller import stop_poller

        await stop_poller()
    except Exception:
        logger.debug("poller_stop skipped")

    try:
        from rmi_rag.observability import _langfuse as langfuse_client

        langfuse_client.flush()
    except Exception:
        logger.debug("langfuse_flush skipped (not configured)")

    _health_state["status"] = "stopped"
    logger.info("rmi_rag_stopped")


# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="RMI RAG API",
    description="Hybrid search, embeddings, chunking, and agentic retrieval for crypto intelligence",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── Auth middleware (before CORS — authenticate before processing) ────
app.add_middleware(RAGAuthMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────
# Match rmi-backend pattern: secure defaults, allow *.rugmunch.io in prod
_cors_origins = CORS_ORIGINS.split(",") if CORS_ORIGINS != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https?://.*\.rugmunch\.io",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-Payment",
        "X-API-Key",
        "X-CSRF-Token",
    ],
)

# ── Mount routers ─────────────────────────────────────────────────────
app.include_router(rag_router)
app.include_router(scam_check_router)


# ── Health endpoints ──────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health() -> dict[str, Any]:
    """Liveness probe — checks Qdrant + Redis connectivity."""
    # Re-probe Qdrant
    qdrant_ok = False
    try:
        async with httpx.AsyncClient(base_url=_health_state["qdrant_url"], timeout=3.0) as client:
            r = await client.get("/")
            qdrant_ok = r.status_code == 200
    except Exception:
        qdrant_ok = False

    # Re-probe Redis
    redis_ok = False
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=_health_state["redis_host"],
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        await r.ping()
        redis_ok = True
        await r.close()
    except Exception:
        redis_ok = False

    # Check subscriber status
    try:
        from rmi_rag.scanner_subscriber import (
            is_subscriber_running,
            subscriber_stats,
        )

        sub_running = is_subscriber_running()
        sub_stats = subscriber_stats()
    except Exception:
        sub_running = False
        sub_stats = {}

    # Check poller status
    try:
        from rmi_rag.scanner_poller import (
            is_poller_running,
            poller_stats,
        )

        poll_running = is_poller_running()
        poll_stats = poller_stats()
    except Exception:
        poll_running = False
        poll_stats = {}

    return {
        "status": "ok" if (qdrant_ok or redis_ok) else "degraded",
        "qdrant": qdrant_ok,
        "redis": redis_ok,
        "qdrant_url": _health_state["qdrant_url"],
        "redis_host": _health_state["redis_host"],
        "scanner_subscriber": sub_running,
        "subscriber_stats": sub_stats,
        "scanner_poller": poll_running,
        "poller_stats": poll_stats,
    }


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, str]:
    """Readiness probe — lightweight check that the app started."""
    return {
        "status": "ready" if _health_state["status"] == "ok" else "starting",
        "app": "rmi-rag",
    }
