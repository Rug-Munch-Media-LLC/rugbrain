"""
RugBrain (formerly RMI RAG) — FastAPI application entry point.

This module is a composition root and nothing else: build the ``FastAPI``
object, add middleware, mount routers. Every subsystem it once started lives in
``rugbrain.app`` — config, health state, probes, auth, CORS, the boot table,
the lifespan composer, and the HTTP surface.

Usage:
    uvicorn rugbrain.main:app --host 0.0.0.0 --port 8012
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Install optional ``app.*`` integration stubs FIRST — the standalone
# rugbrain container never ships the rmi-backend ``app`` package, so
# ``from app.x import y`` must degrade to a stub instead of raising.
from rugbrain.import_guard import install_import_guard

install_import_guard()

# Deliberately after install_import_guard() so app.* stubs exist first.
from rugbrain.app.auth import RAGAuthMiddleware  # noqa: E402
from rugbrain.app.boot_steps import STEP_NAMES as BOOT_STEP_NAMES  # noqa: E402
from rugbrain.app.cors import cors_kwargs  # noqa: E402
from rugbrain.app.health_routes import router as health_routes_router  # noqa: E402
from rugbrain.app.health_state import STATE  # noqa: E402
from rugbrain.app.lifespan import lifespan  # noqa: E402
from rugbrain.app.routes_registry import include_all_routers  # noqa: E402

logger = logging.getLogger(__name__)

#: The process health dict, re-exported under its historical private name.
#: ``main._health_state`` was the pre-split access path; keeping the alias means
#: a mutable, shared reference (NOT a copy) so existing callers keep working.
_health_state = STATE

__all__ = [
    "BOOT_STEP_NAMES",
    "RAGAuthMiddleware",
    "_health_state",
    "app",
    "health_routes_router",
    "include_all_routers",
    "lifespan",
]

#: The background subsystems, in boot order. Public so tests can assert the
#: split preserved the pre-refactor startup sequence exactly.
assert len(BOOT_STEP_NAMES) == 27, f"expected 27 boot steps, got {len(BOOT_STEP_NAMES)}"

app = FastAPI(
    title="RugBrain API",
    description=(
        "Hybrid search, embeddings, chunking, and agentic retrieval for "
        "crypto intelligence (formerly RMI RAG)"
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Auth before CORS — authenticate before processing.
app.add_middleware(RAGAuthMiddleware)
app.add_middleware(CORSMiddleware, **cors_kwargs())

include_all_routers(app)
app.include_router(health_routes_router)
