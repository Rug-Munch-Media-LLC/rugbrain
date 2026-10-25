"""Single JSON GET helper: 8 s timeout, one retry, typed ProviderError."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from .envelope import iso, utcnow

TIMEOUT_S = float(os.getenv("LIVE_HTTP_TIMEOUT", "8"))
MAX_RETRIES = int(os.getenv("LIVE_HTTP_RETRIES", "1"))


class ProviderError(Exception):
    """Typed provider failure. ``provider`` names who failed, never 'unknown'."""

    def __init__(self, provider: str, code: str, reason: str) -> None:
        self.provider = provider
        self.code = code
        self.reason = reason
        super().__init__(f"{provider}: {code}: {reason}")


def classify(provider: str, exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(provider, "timeout", f"timed out after {TIMEOUT_S:.0f}s")
    if isinstance(exc, httpx.HTTPStatusError):
        return ProviderError(provider, f"http_{exc.response.status_code}", exc.response.text[:180])
    if isinstance(exc, json.JSONDecodeError):
        return ProviderError(provider, "bad_payload", "response was not JSON")
    return ProviderError(provider, "transport_error", type(exc).__name__)


async def get_json(
    url: str,
    *,
    provider: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[Any, str]:
    """GET JSON with one retry. Raises ProviderError naming the provider."""
    delay = 0.25
    last: ProviderError | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            if client is None:
                async with httpx.AsyncClient() as own:
                    resp = await own.get(url, params=params, headers=headers, timeout=timeout or TIMEOUT_S)
            else:
                resp = await client.get(url, params=params, headers=headers, timeout=timeout or TIMEOUT_S)
            resp.raise_for_status()
            return resp.json(), iso(utcnow())
        except Exception as exc:
            last = classify(provider, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2
    assert last is not None
    raise last
