"""Fail-open provider circuit breaker — skip a 402/401/403 lane instantly.

One responsibility: remember which hosted provider last answered with a
billing/auth error, so the next call can skip it in O(1) instead of paying the
retry ladder (measured cold tail: 3+4+8+16 s against DeepInfra 402 overdue
invoices).  Fail-open by construction: every function swallows its own errors,
records them at DEBUG, and an unreadable breaker answers "not dead", so a
breaker bug can never block retrieval — but it also never disappears unnoticed.
"""

from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

#: provider name -> epoch seconds at which the breaker closes again
_DEAD_PROVIDERS: dict[str, float] = {}
_LOCK = threading.Lock()
DEFAULT_TTL_S = 600.0
_ENV = "PROVIDER_BREAKER_S"


def _ttl() -> float:
    try:
        return float(os.environ.get(_ENV, DEFAULT_TTL_S))
    except (TypeError, ValueError) as exc:
        log.debug("breaker_ttl_invalid env=%s value=%r err=%s", _ENV, os.environ.get(_ENV), exc)
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
    except Exception as exc:
        log.debug("breaker_is_dead_failed provider=%s err=%s", provider, exc)
        return False


def mark_dead(provider: str, seconds: float | None = None) -> None:
    """Park *provider* for ``seconds`` (default ``PROVIDER_BREAKER_S``/600)."""
    try:
        with _LOCK:
            _DEAD_PROVIDERS[provider] = time.time() + (seconds if seconds is not None else _ttl())
    except Exception as exc:
        log.debug("breaker_mark_dead_failed provider=%s err=%s", provider, exc)


def clear(provider: str) -> None:
    """Close the breaker after a success."""
    try:
        with _LOCK:
            _DEAD_PROVIDERS.pop(provider, None)
    except Exception as exc:
        log.debug("breaker_clear_failed provider=%s err=%s", provider, exc)


def snapshot() -> dict[str, float]:
    """Remaining seconds per parked provider (diagnostics only)."""
    try:
        now = time.time()
        with _LOCK:
            return {k: round(v - now, 1) for k, v in _DEAD_PROVIDERS.items() if v > now}
    except Exception as exc:
        log.debug("breaker_snapshot_failed err=%s", exc)
        return {}
