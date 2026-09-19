"""API-facing helpers: current snapshot + window samples, current value."""

from __future__ import annotations

from typing import Any


async def get_fear_index(window: str = "24h", force: bool = False) -> dict[str, Any]:
    """Return the current snapshot + window samples (fail-open).

    Resolution order: Redis cache → in-process memory → live poll.
    """
    from rugbrain import fear_index as _pkg

    source = "cache"
    snapshot: dict[str, Any] | None = None
    if not force:
        snapshot = await _pkg._cache_get()
        if snapshot is None:
            snapshot = _pkg._last_snapshot
            source = "memory"
    if snapshot is None:
        snapshot = await _pkg.poll_fear_index(use_cache=False)
        source = "fresh"
    samples = await _pkg._samples_for_window(window)
    return {
        **snapshot,
        "window": window,
        "samples": samples,
        "source": source,
    }


async def current_fear_value() -> int | None:
    """Current fear index value (0-100), None when unavailable."""
    from rugbrain import fear_index as _pkg

    snapshot = await _pkg.get_fear_index()
    value = snapshot.get("value")
    return int(value) if isinstance(value, (int, float)) else None
