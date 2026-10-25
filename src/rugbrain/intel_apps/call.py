"""Deadline + refusal wrapper shared by the three intel adapters."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from rugbrain.intel_apps.envelope import STATUS_OK, STATUS_REFUSED, envelope

DEFAULT_TIMEOUT_S = 8.0
Invoke = Callable[[str, dict[str, Any]], Awaitable[Any]]


async def guarded_call(
    *,
    provider: str,
    tool: str,
    invoke: Invoke,
    args: dict[str, Any],
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Call one provider tool under a hard deadline; never raise past here."""
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    def refused(code: str, reason: str) -> dict[str, Any]:
        return envelope(
            provider=provider,
            tool=tool,
            status=STATUS_REFUSED,
            latency_ms=elapsed(),
            code=code,
            reason=reason,
        )

    try:
        data = await asyncio.wait_for(invoke(tool, args), timeout=timeout_s)
    except TimeoutError:
        return refused("timeout", f"{provider}.{tool} exceeded {timeout_s:.1f}s deadline")
    except Exception as exc:  # provider transport may raise anything
        return refused("provider_error", f"{provider}.{tool} failed: {type(exc).__name__}")
    if data is None or data == {} or data == "":
        return refused("empty_result", f"{provider}.{tool} returned no usable result")
    if isinstance(data, dict) and data.get("error"):
        upstream = str(data["error"])[:120]
        return refused("upstream_error", f"{provider}.{tool} upstream: {upstream}")
    return envelope(
        provider=provider, tool=tool, status=STATUS_OK, latency_ms=elapsed(), data=data
    )
