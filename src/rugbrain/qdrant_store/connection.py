"""Qdrant connection resolution.

Connection strategy:
  1. Use QDRANT_URL env var (default: http://rmi-qdrant-v2:6334 (v2))
  2. Fallback to http://127.0.0.1:{QDRANT_PORT or 6334} for local dev
  3. Fallback to http://localhost:{QDRANT_PORT or 6334}
  First URL that responds to GET / is used for the session.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# ── Connection resolution ──────────────────────────────────────────────

QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant-v2:6334")
QDRANT_FALLBACK_URLS: list[str] = []

# Build fallback chain: primary → loopback variants
_primary = QDRANT_URL.rstrip("/")
_fallback_port = os.getenv("QDRANT_PORT", "6334")
QDRANT_FALLBACK_URLS = [
    _primary,
    f"http://127.0.0.1:{_fallback_port}",
    f"http://localhost:{_fallback_port}",
]
# Deduplicate while preserving order
_seen: set[str] = set()
QDRANT_FALLBACK_URLS = [
    u
    for u in QDRANT_FALLBACK_URLS
    if u not in _seen and not _seen.add(u)  # type: ignore[func-returns-value]
]

_resolved_url: str | None = None
_resolved_lock = asyncio.Lock()


async def _resolve_qdrant_url() -> str:
    """Probe fallback URLs and return the first responsive one.

    Caches the resolved URL so we only probe once per process lifetime.
    """
    global _resolved_url
    if _resolved_url is not None:
        return _resolved_url

    async with _resolved_lock:
        cached = _resolved_url
        if cached is not None:
            return cached  # type: ignore[unreachable]  # double-checked locking across coroutines

        for url in QDRANT_FALLBACK_URLS:
            try:
                async with httpx.AsyncClient(base_url=url, timeout=3.0) as c:
                    r = await c.get("/")
                    if r.status_code == 200:
                        _resolved_url = url
                        logger.info("qdrant_resolved url=%s", url)
                        return url
            except Exception:
                logger.debug("qdrant_probe_failed url=%s", url)
                continue

        # None worked — use primary anyway; callers will handle errors
        _resolved_url = _primary
        logger.warning("qdrant_unreachable using_fallback=%s", _resolved_url)
        return _resolved_url


async def _probe_qdrant() -> bool:
    """Simple boolean health probe — used by /health endpoint."""
    try:
        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=3.0) as c:
            r = await c.get("/")
            return r.status_code == 200
    except Exception:
        return False
