"""Rate limiter for LunarCrush API requests (1 req/sec free tier)."""

from __future__ import annotations

import asyncio
import time


async def _throttle() -> None:
    """Enforce rate limit: ensure at least MIN_REQUEST_GAP between requests."""
    from rugbrain.monitors import social_sentiment as _pkg

    async with _pkg._rate_limit_lock:
        elapsed = time.time() - _pkg._last_request_time
        if elapsed < _pkg.MIN_REQUEST_GAP:
            await asyncio.sleep(_pkg.MIN_REQUEST_GAP - elapsed)
        _pkg._last_request_time = time.time()
