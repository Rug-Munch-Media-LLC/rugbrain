"""Rate-limited HTTP client for the LunarCrush API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("rugbrain.monitors.social_sentiment")


async def _api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Make a rate-limited GET request to LunarCrush API."""
    from rugbrain.monitors import social_sentiment as _pkg

    if not _pkg.API_KEY:
        logger.debug("lc_api_skipped no_api_key path=%s", path)
        return None

    await _pkg._throttle()

    url = f"{_pkg.API_BASE}{path}"
    headers = {"Accept": "application/json"}
    query_params = dict(params or {})
    query_params.setdefault("key", _pkg.API_KEY)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=query_params, headers=headers)
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                logger.warning(
                    "lc_rate_limited path=%s retry_after=%s",
                    path,
                    r.headers.get("retry-after", "?"),
                )
                return None
            else:
                logger.debug("lc_api_error path=%s status=%d", path, r.status_code)
                return None
    except httpx.TimeoutException:
        logger.debug("lc_api_timeout path=%s", path)
        return None
    except Exception as exc:
        logger.debug("lc_api_failed path=%s err=%s", path, str(exc)[:80])
        return None
