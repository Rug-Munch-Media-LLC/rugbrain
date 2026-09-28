"""Lifespan — compose startup and shutdown from the boot table.

One job: run every ``BootStep`` in order, record the roll-up status, and on
shutdown stop what has a stop handle. It contains no subsystem knowledge —
that lives in ``boot_steps`` as data.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from rugbrain.app import probes
from rugbrain.app.boot_steps import BOOT_STEPS, STOP_STEPS
from rugbrain.app.config import OTEL_ENABLED, OTEL_EXPORTER_OTLP_ENDPOINT
from rugbrain.app.health_state import STATE, set_flag

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _setup_otel() -> None:
    """Import the OTel bridge when configured; never fail boot."""
    if not (OTEL_ENABLED and OTEL_EXPORTER_OTLP_ENDPOINT):
        return
    try:
        from rugbrain.observability import _langfuse  # noqa: F401

        logger.info("OTel observability enabled exporter=%s", OTEL_EXPORTER_OTLP_ENDPOINT)
    except Exception as exc:
        logger.warning("OTel setup skipped: %s", exc)


def _warm_state() -> None:
    """Reset the health dict to its starting shape for this process."""
    STATE["status"] = "starting"
    STATE["qdrant_url"] = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
    STATE["redis_host"] = os.getenv("REDIS_HOST", "localhost")


async def _probe_dependencies() -> None:
    """One startup reachability pass over Qdrant and Redis."""
    set_flag("qdrant", await probes.probe_qdrant(STATE["qdrant_url"]))
    set_flag("redis", await probes.probe_redis())


async def _run_boot_steps() -> None:
    """Start every subsystem. A failing step is logged, never fatal."""
    for step in BOOT_STEPS:
        try:
            await step.run()
        except Exception as exc:
            logger.warning("%s_start_failed: %s", step.name, exc)
            set_flag(step.name, False)


def _record_rollup() -> None:
    """status=ok only when Qdrant, its schema, and Redis are all healthy."""
    STATE["status"] = (
        "ok" if STATE["qdrant"] and STATE["redis"] and STATE["qdrant_schema"] else "degraded"
    )
    logger.info(
        "rugbrain_started qdrant=%s schema=%s redis=%s status=%s",
        STATE["qdrant"],
        STATE["qdrant_schema"],
        STATE["redis"],
        STATE["status"],
    )


async def _shutdown() -> None:
    """Stop managed subsystems in shutdown order, flush OTel, mark stopped."""
    for step in STOP_STEPS:
        if step.stop is None:  # pragma: no cover - STOP_STEPS is filtered
            continue
        try:
            await step.stop()
        except Exception:
            logger.debug("%s stop skipped", step.name)
    try:
        from rugbrain.observability import _langfuse as langfuse_client

        langfuse_client.flush()
    except Exception:
        logger.debug("langfuse_flush skipped (not configured)")
    STATE["status"] = "stopped"
    logger.info("rugbrain_stopped")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup: OTel, warm state, probe, boot every subsystem."""
    _setup_otel()
    _warm_state()
    await _probe_dependencies()
    logger.info(
        "rugbrain_starting qdrant=%s redis=%s",
        STATE["qdrant"],
        STATE["redis"],
    )
    await _run_boot_steps()
    _record_rollup()
    yield
    await _shutdown()
