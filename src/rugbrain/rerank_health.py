"""Reranker endpoint health cache and probe.

Owns the mutable ``_health``/``_lock`` state for the decoupled Qwen3 reranker
client so the health ledger has exactly one home. Kept in its own module
because it is a distinct job (reachability bookkeeping) from ranking
(``rugbrain.reranker``) and instruction framing (``rugbrain.rerank_instructions``).

Reads the endpoint URL lazily via a getter so tests and config reloads are
picked up without a stale module-level constant.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Mutable module state — the whole reason this module exists. Only one copy
# in the process; every consumer reads it through here.
_health: dict[str, Any] = {"ok": False, "last_check": 0.0, "latency_ms": 0}
_lock = asyncio.Lock()

# Probe freshness windows (seconds). "Recently ok" is trusted for 30s;
# "recently down" is trusted for 60s so we do not hammer a dead server.
_OK_TTL_S = 30.0
_DOWN_TTL_S = 60.0


def url_getter() -> Any:
    """Resolve the current endpoint URL from ``rugbrain.reranker``.

    Imported lazily (not at module scope) so ``reranker`` can import this
    module without a cycle.
    """
    from rugbrain import reranker

    return reranker.RERANKER_URL


def enabled_getter() -> Any:
    """Resolve the current ``RERANKER_ENABLED`` flag from ``rugbrain.reranker``."""
    from rugbrain import reranker

    return reranker.RERANKER_ENABLED


def cache() -> dict[str, Any]:
    """Return the live health cache dict (mutable — tests and callers share it)."""
    return _health


async def _fast_health() -> bool:
    """Fast cached health check — avoids hammering a down server."""
    async with _lock:
        if _health["ok"] and (time.time() - _health["last_check"]) < _OK_TTL_S:
            return True
        if not _health["ok"] and (time.time() - _health["last_check"]) < _DOWN_TTL_S:
            return False
    return True  # Allow the actual request to probe


async def _mark(ok: bool, latency_ms: float | None = None) -> None:
    """Record a reachability result into the cache under the lock."""
    async with _lock:
        _health["ok"] = ok
        _health["last_check"] = time.time()
        if latency_ms is not None:
            _health["latency_ms"] = round(latency_ms, 1)


async def get_reranker_health() -> dict[str, Any]:
    """Check if the reranker server is healthy.

    Returns:
        Dict with ok (bool), latency_ms (float), model (str or None).
    """
    if not enabled_getter():
        return {"ok": False, "enabled": False, "reason": "RERANKER_ENABLED=false"}

    url = url_getter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            t0 = time.monotonic()
            resp = await client.get(f"{url}/health")
            t1 = time.monotonic()

            if resp.status_code == 200:
                data = resp.json()
                await _mark(True, (t1 - t0) * 1000)
                return {
                    "ok": True,
                    "enabled": True,
                    "latency_ms": _health["latency_ms"],
                    "model": data.get("model", "unknown"),
                }

            await _mark(False)
            return {"ok": False, "enabled": True, "status_code": resp.status_code}

    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        await _mark(False)
        return {"ok": False, "enabled": True, "error": str(exc)}


async def reranker_health_loop() -> None:
    """Background health check loop — updates the _health cache every 60s."""
    while True:
        await get_reranker_health()
        await asyncio.sleep(60)
