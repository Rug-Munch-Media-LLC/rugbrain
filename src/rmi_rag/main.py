"""
RMI RAG — FastAPI application entry point.

Mounts the existing APIRouter from endpoints.py, adds health/readiness probes,
CORS middleware, and OpenTelemetry integration via observability.py.

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
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from rmi_rag.endpoints import router as rag_router
from rmi_rag.endpoints import scam_check_router

logger = logging.getLogger(__name__)

# ── Env ──────────────────────────────────────────────────────────────
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "").lower() in ("1", "true", "yes")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

# ── Health probes (shared state) ─────────────────────────────────────
_health_state: dict[str, Any] = {
    "status": "starting",
    "qdrant": False,
    "redis": False,
    "qdrant_url": "",
    "redis_host": "",
}


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
    _health_state["qdrant_url"] = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333").rstrip("/")
    _health_state["redis_host"] = os.getenv("REDIS_HOST", "rmi-redis")

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

    _health_state["status"] = "ok"
    logger.info(
        "rmi_rag_started qdrant=%s redis=%s",
        _health_state["qdrant"],
        _health_state["redis"],
    )

    yield

    # ── Shutdown ──────────────────────────────────────────────────
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

    return {
        "status": "ok" if (qdrant_ok or redis_ok) else "degraded",
        "qdrant": qdrant_ok,
        "redis": redis_ok,
        "qdrant_url": _health_state["qdrant_url"],
        "redis_host": _health_state["redis_host"],
    }


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, str]:
    """Readiness probe — lightweight check that the app started."""
    return {
        "status": "ready" if _health_state["status"] == "ok" else "starting",
        "app": "rmi-rag",
    }
