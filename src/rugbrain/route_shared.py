"""Shared route plumbing (leaf).

Extracted from ``rugbrain.endpoints`` so route modules no longer import the
router-assembly module (``endpoints -> routes -> endpoints`` cycle).
"""

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_TOKEN_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

RATE_LIMITS = {
    "free": 5,
    "pro": 100,
    # /api/v1/search is called by the backend scanner per token over a shared
    # egress IP, so the per-IP limit must be generous but still bound abuse
    # (each search costs an embedding + Qdrant query + optional rerank).
    "search": int(os.getenv("RAG_SEARCH_RATE_LIMIT", "3600")),
}


async def _check_rate_limit(identifier: str, tier: str, hour_key: str) -> tuple[bool, int]:
    """Check the per-identifier hourly rate limit.

    Fails OPEN when Redis is unavailable: rate limiting is a best-effort
    guard, not a hard dependency — the route should still serve results
    when the limiter backend is down.
    """
    from rugbrain.search_service import _get_redis

    try:
        rd = await _get_redis()
        limit = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
        key = f"rag:ratelimit:{tier}:{identifier}:{hour_key}"
        current = await rd.incr(key)
        if current == 1:
            now = datetime.now(UTC)
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            ttl = int((next_hour - now).total_seconds())
            await rd.expire(key, ttl)
        else:
            ttl = await rd.ttl(key)
            if ttl < 0:
                ttl = 3600
        if current > limit:
            return False, ttl
        return True, ttl
    except Exception as exc:
        logger.warning("rate_limit_unavailable: failing open — %s", exc)
        return True, 3600


def _is_token_address(q: str) -> bool:
    """True when *q* is a bare token/contract address (0x + 40 hex)."""
    return bool(_TOKEN_ADDRESS_RE.fullmatch((q or "").strip()))


class _EndpointsProxy:
    """Live proxy for ``rugbrain.endpoints`` (defers the import to attribute access).

    Route modules used to do ``from rugbrain import endpoints as _ep`` at module
    level, which closed the ``endpoints -> routes -> endpoints`` import cycle.
    The proxy keeps the exact call-time resolution semantics without the edge.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        from rugbrain import endpoints as _module

        return getattr(_module, name)


endpoints_proxy: Any = _EndpointsProxy()
