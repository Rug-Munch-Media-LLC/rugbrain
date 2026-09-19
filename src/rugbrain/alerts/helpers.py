"""Source enable/disable + rate-limit helpers."""

from __future__ import annotations

import os

from .state import AlertState


def _enabled(name: str) -> bool:
    return os.getenv(f"ALERT_{name.upper()}_ENABLED", "true").lower() in ("1", "true", "yes")


def _rate_limited(state: AlertState, source: str, now: float, interval: float) -> bool:
    """Return True when *source* was polled within ``interval`` seconds.

    The poll loop uses the async ``AlertState.last_poll``; this pure helper
    backs the in-memory state used in tests / minimal environments.
    """
    last = getattr(state, "_memory", {}).get(f"poll:{source}")
    if last is None:
        return False
    return (now - float(last)) < interval
