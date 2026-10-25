"""Opt-in retry for one GET: a flaky upstream is a result, never a crash.

One job — bounded exponential backoff over the status codes that mean "come
back later", returning the response or ``None`` with the reason logged.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


async def get(
    client: Any,
    url: str,
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
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
            await asyncio.sleep(base_delay * (2**attempt))
            continue
        if response.status_code in RETRY_STATUS and not last:
            await asyncio.sleep(base_delay * (2**attempt))
            continue
        return response
    return None


__all__ = ["RETRY_STATUS", "get"]
