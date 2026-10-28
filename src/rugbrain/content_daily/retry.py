"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

Bounded, classified retry — the only place a stage is attempted more than once.

Retry policy lives in the contract (:mod:`rugbrain.content_daily.spec`); this
module only executes it. Two rules matter:

*   An error is classified before it is retried. A schema violation is not a
    transient fault, and retrying it three times just multiplies the log noise.
*   A ``429`` carries the server's own pacing. Honouring ``Retry-After`` is
    what makes the Telegram stage survive a channel-wide flood limit instead
    of burning every attempt inside one second.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from rugbrain.content_daily.spec import RetryPolicy, StageSpec

#: Never wait longer than this, whatever the server asks for.
RETRY_AFTER_CAP = 30.0


class StageError(RuntimeError):
    """A stage ran out of attempts. Carries the classification and the trail."""

    def __init__(
        self,
        stage: str,
        error_class: str,
        attempts: list[dict[str, Any]],
        last: str,
    ) -> None:
        super().__init__(f"{stage} failed after {len(attempts)} attempt(s): {last}")
        self.stage = stage
        self.error_class = error_class
        self.attempts = attempts
        self.last = last


def classify(exc: BaseException) -> str:
    """Map an exception to the vocabulary the retry policies speak."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        if status == 429:
            return "http_429"
        if status >= 500:
            return "http_5xx"
        if status >= 400:
            return "http_4xx"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "value"
    if isinstance(exc, (ConnectionError, OSError)):
        return "connection"
    return "unknown"


def _delay(policy: RetryPolicy, attempt_number: int, exc: BaseException) -> float:
    """Exponential backoff, or the server's ``Retry-After`` when it gave one."""
    requested = getattr(exc, "retry_after", None)
    if isinstance(requested, (int, float)) and requested >= 0:
        return min(float(requested), RETRY_AFTER_CAP)
    backoff = policy.base_delay * (2 ** (attempt_number - 1))
    return min(backoff, policy.max_delay) if policy.max_delay else backoff


async def attempt(
    stage: StageSpec,
    fn: Callable[[], Awaitable[Any]],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[Any, list[dict[str, Any]]]:
    """Run ``fn`` under ``stage.policy``; return ``(value, attempt_trail)``.

    Raises :class:`StageError` when attempts are exhausted or the failure
    class is not retryable. The trail is returned even on success so the
    journal can record how many tries a stage actually needed.
    """
    policy = stage.policy
    total = max(1, policy.attempts)
    trail: list[dict[str, Any]] = []

    for number in range(1, total + 1):
        started = time.perf_counter()
        try:
            if policy.timeout:
                value = await asyncio.wait_for(fn(), timeout=policy.timeout)
            else:
                value = await fn()
        except Exception as exc:
            error_class = classify(exc)
            trail.append(
                {
                    "attempt": number,
                    "ok": False,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                    "error_class": error_class,
                    "error": str(exc)[:300],
                }
            )
            if number >= total or not policy.wants_retry(error_class):
                raise StageError(stage.name, error_class, trail, str(exc)[:300]) from exc
            await sleep(_delay(policy, number, exc))
            continue
        trail.append(
            {
                "attempt": number,
                "ok": True,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
        return value, trail

    raise StageError(stage.name, "unknown", trail, "no attempt executed")  # pragma: no cover


__all__ = ["RETRY_AFTER_CAP", "StageError", "attempt", "classify"]
