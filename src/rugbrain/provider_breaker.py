"""Fail-open provider circuit breaker — skip a 402/401/403 lane instantly.

One responsibility: remember which hosted provider last answered with a
billing/auth error, so the next call can skip it in O(1) instead of paying the
retry ladder (measured cold tail: 3+4+8+16 s against DeepInfra 402 overdue
invoices).  Fail-open by construction: every function swallows its own errors
and an unreadable breaker answers "not dead", so a breaker bug can never block
retrieval.
"""

from __future__ import annotations

import os
import threading
import time

#: provider name -> epoch seconds at which the breaker closes again
_DEAD_PROVIDERS: dict[str, float] = {}
_LOCK = threading.Lock()
DEFAULT_TTL_S = 600.0
_ENV = "PROVIDER_BREAKER_S"


def _ttl() -> float:
    try:
        return float(os.environ.get(_ENV, DEFAULT_TTL_S))
    except (TypeError, ValueError):
        return DEFAULT_TTL_S


def is_dead(provider: str) -> bool:
    """True while *provider* is parked.  False on any internal error (fail-open)."""
    try:
        with _LOCK:
            until = _DEAD_PROVIDERS.get(provider)
            if until is None:
                return False
            if time.time() >= until:
                _DEAD_PROVIDERS.pop(provider, None)
                return False
            return True
    except Exception:  # noqa: BLE001 — a breaker bug must never block retrieval
        return False


def mark_dead(provider: str, seconds: float | None = None) -> None:
    """Park *provider* for ``seconds`` (default ``PROVIDER_BREAKER_S``/600)."""
    try:
        with _LOCK:
            _DEAD_PROVIDERS[provider] = time.time() + (
                seconds if seconds is not None else _ttl()
            )
    except Exception:  # noqa: BLE001
        pass


def clear(provider: str) -> None:
    """Close the breaker after a success."""
    try:
        with _LOCK:
            _DEAD_PROVIDERS.pop(provider, None)
    except Exception:  # noqa: BLE001
        pass


def snapshot() -> dict[str, float]:
    """Remaining seconds per parked provider (diagnostics only)."""
    try:
        now = time.time()
        with _LOCK:
            return {k: round(v - now, 1) for k, v in _DEAD_PROVIDERS.items() if v > now}
    except Exception:  # noqa: BLE001
        return {}
