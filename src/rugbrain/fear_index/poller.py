"""Snapshot polling and the background asyncio poller lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import httpx

log = logging.getLogger("rugbrain.fear_index")


async def poll_fear_index(use_cache: bool = True) -> dict[str, Any]:
    """Fetch all components, compute, record, persist and cache a snapshot."""
    from rugbrain import fear_index as _pkg

    if use_cache:
        cached = await _pkg._cache_get()
        if cached:
            return cached
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            _pkg.fetch_fng(client),
            _pkg.fetch_social_sentiment(client),
            _pkg.fetch_onchain_signals(client),
            _pkg.fetch_news_sentiment(client),
            return_exceptions=True,
        )
    components: dict[str, int | None] = {
        "fng": _pkg._as_int_or_none(results[0]),
        "social": _pkg._as_int_or_none(results[1]),
        "onchain": _pkg._as_int_or_none(results[2]),
        "news": _pkg._as_int_or_none(results[3]),
    }
    snapshot = _pkg.fear_snapshot(components)
    _pkg._record(snapshot)
    _pkg._storage.insert(snapshot)
    await _pkg._cache_set(snapshot)
    return snapshot


async def _poller_loop() -> None:
    from rugbrain import fear_index as _pkg

    while True:
        try:
            await _pkg.poll_fear_index()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("fear_index_poll_failed")
        await asyncio.sleep(_pkg.FEAR_INDEX_POLL_SECONDS)


async def start_fear_index_poller() -> bool:
    """Start the background poller (idempotent)."""
    from rugbrain import fear_index as _pkg

    async with _pkg._poller_lock:
        if _pkg._poller_task is not None and not _pkg._poller_task.done():
            return True
        _pkg._poller_task = asyncio.create_task(_poller_loop(), name="fear-index-poller")
        return True


async def stop_fear_index_poller() -> None:
    """Cancel the background poller (idempotent)."""
    from rugbrain import fear_index as _pkg

    task = _pkg._poller_task
    _pkg._poller_task = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def is_fear_index_poller_running() -> bool:
    """True when the poller task is active."""
    from rugbrain import fear_index as _pkg

    task = _pkg._poller_task
    return task is not None and not task.done()
