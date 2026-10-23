"""Free-lane fan-out: recent tweets for many handles via Pry profile scrapes.

Moved out of ``pipeline`` (which the structure gate holds to 200 lines) so the
orchestrator only sequences stages. ``pipeline`` re-exports ``fetch_pry_tweets``
under its historical ``_fetch_pry_tweets`` name for existing importers.

A payment-gated lane (x402 402) on EVERY handle is structural, not transient, so
it is reported as ``dead=True`` instead of being swallowed as "nothing new":
the runner must exit nonzero rather than publish an empty green cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("rugbrain.x_intel")

#: ``pry_lane.ScrapeFn`` — (url, transport) → parsed profile dict or None.
ScrapeProfileFn = Callable[..., Awaitable[dict[str, Any] | None]]


async def fetch_pry_tweets(
    handles: list[str], scrape: ScrapeProfileFn | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Pull recent tweets for *handles* via free Pry profile scrapes.

    Returns ``(tweets, dead)``; ``dead`` is True when the lane came back
    payment-gated (402) for every handle — nothing scraped *and* the cause is
    actionable. Transient per-handle errors still fail-open. ``scrape`` is the
    DI seam; default is the real Pry lane.
    """
    from rugbrain.ingest_sources.x_intel.pry_lane import PryLaneUnavailable, scrape_profile

    fetch = scrape or scrape_profile
    out: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(3)
    dead = 0

    async def one(handle: str) -> bool:
        nonlocal dead
        async with sem:
            try:
                profile = await fetch(handle)
            except PryLaneUnavailable:
                dead += 1
                return True
            except Exception as exc:
                logger.debug("xintel:pry_profile_failed handle=%s err=%s", handle, str(exc)[:120])
                return False
            if profile:
                out.extend(profile.get("recent_tweets") or [])
        return False

    await asyncio.gather(*(one(h) for h in handles))
    return out, bool(handles) and dead == len(handles)


__all__ = ["ScrapeProfileFn", "fetch_pry_tweets"]
