"""Opt-in retry for one GET: a flaky upstream is a result, never a crash.

One job — bounded exponential backoff over the status codes that mean "come
back later", returning the response or ``None`` with the reason logged. A
``Retry-After`` header wins over the exponential table (floor, not ceiling:
we never sleep less than the server asked).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Ceiling for a single wait, header or not.
MAX_DELAY = 30.0


async def get(
    client: Any,
    url: str,
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = MAX_DELAY,
    **kwargs: Any,
) -> Any | None:
    """GET *url* with backoff; ``None`` when every attempt failed."""
    for attempt in range(attempts):
        last = attempt + 1 >= attempts
        try:
            response = await client.get(url, **kwargs)
        except Exception as exc:  # transport failure: retry or report
            if last:
                log.warning("http_get_failed url=%s err=%s", url, str(exc)[:200])
                return None
            await asyncio.sleep(min(base_delay * (2**attempt), max_delay))
            continue
        if response.status_code in RETRY_STATUS and not last:
            delay = min(base_delay * (2**attempt), max_delay)
            header = str(response.headers.get("Retry-After") or "").strip()
            if header:
                try:
                    delay = min(max(float(header), 0.0), max_delay)
                except ValueError:
                    log.info("retry_after_unparsed value=%s url=%s", header[:32], url)
            await asyncio.sleep(delay)
            continue
        return response
    return None


__all__ = ["MAX_DELAY", "RETRY_STATUS", "get"]
