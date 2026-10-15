"""X social-intel cycle — fetch → dedupe → embed → news_v2.

``run_cycle`` orchestrates: GetXAPI timelines for TIER 1/2 (budget-gated,
round-robin), free Pry profile scrapes for TIER 3/4, then Redis-seen dedupe and
a Qdrant upsert (``storage``). Fail-open: every *transient* stage degrades to
zero counts. The Pry lane is the exception — an x402 402 is structural and
propagates as ``PryLaneUnavailable`` so the runner exits nonzero instead of
reporting a green empty cycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from rugbrain.ingest_sources.x_intel.dedupe import dedupe_seen
from rugbrain.ingest_sources.x_intel.pry_lane import PryLaneUnavailable
from rugbrain.ingest_sources.x_intel.routing import route_account, should_use_getxapi
from rugbrain.ingest_sources.x_intel.storage import store_docs

logger = logging.getLogger("rugbrain.x_intel")

#: DI seams: account selection, and the Pry fetch (tweets, dead-lane flag).
SelectFn = Callable[[], Awaitable[tuple[list[str], list[str]]]]
FetchPryFn = Callable[[list[str]], Awaitable[tuple[list[dict[str, Any]], bool]]]
#: ``pry_lane.ScrapeFn`` — (url, transport) → parsed data dict or None.
ScrapeProfileFn = Callable[..., Awaitable[dict[str, Any] | None]]


async def _fetch_getxapi_tweets(handles: list[str], count: int) -> list[dict[str, Any]]:
    """Pull timelines for *handles* via GetXAPI (1 call each, fail-open)."""
    from rugbrain.ingest_sources.x_intel.client import GetXAPIClient

    client = GetXAPIClient()
    out: list[dict[str, Any]] = []
    for handle in handles:
        try:
            batch = await client.user_tweets(handle, count=count)
        except Exception as exc:
            logger.debug("xintel:timeline_failed handle=%s err=%s", handle, str(exc)[:120])
            continue
        for tweet in batch:
            if isinstance(tweet, dict):
                if not str(tweet.get("handle") or "").strip():
                    # GetXAPI omits the author, so its URL is the generic
                    # /i/status/ form; rebuild it so the doc is attributable.
                    tweet["handle"] = handle
                    tid = str(tweet.get("tweet_id") or "").strip()
                    if tid:
                        tweet["url"] = f"https://x.com/{handle}/status/{tid}"
                out.append(tweet)
    return out


async def _fetch_pry_tweets(
    handles: list[str], scrape: ScrapeProfileFn | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Pull recent tweets for *handles* via free Pry profile scrapes.

    Returns ``(tweets, dead)``; ``dead`` is True when the lane came back
    payment-gated (402) for every handle — nothing scraped *and* the cause is
    actionable. Transient per-handle errors still fail-open. ``scrape`` is the
    DI seam; default is the real Pry lane.
    """
    from rugbrain.ingest_sources.x_intel.pry_lane import scrape_profile

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


async def _select_accounts() -> tuple[list[str], list[str]]:
    """Split this cycle's accounts into (getxapi_handles, pry_handles).

    Round-robin over TIER 1/2 (GetXAPI lane, budget-bounded); TIER 3/4
    handles route to Pry per ``route_account``.
    """
    from rugbrain.compat import get_redis_async
    from rugbrain.ingest_sources.x_intel.config import (
        MAX_ACCOUNTS_PER_CYCLE,
        TIER1_ACCOUNTS,
        TIER2_ACCOUNTS,
    )

    tier12 = list(TIER1_ACCOUNTS) + list(TIER2_ACCOUNTS)
    try:
        r = await get_redis_async()
        offset = int(await r.incr("xintel:rotation_offset") or 0)
    except Exception as exc:
        logger.debug("xintel:rotation_redis_failed err=%s", str(exc)[:120])
        offset = 0
    batch = (
        [
            tier12[(offset + i) % len(tier12)]
            for i in range(min(MAX_ACCOUNTS_PER_CYCLE, len(tier12)))
        ]
        if tier12
        else []
    )
    # Lane decision for the whole batch. While pacing is active or the
    # GetXAPI budget is exhausted (plan credits expired 2026-10-02), TIER1/2
    # falls to the free Pry lane — otherwise every cycle fetches nothing
    # while the paid lane is dead. Fail-open: Redis down → paid lane.
    use_gx = await should_use_getxapi("user-tweets")
    if use_gx:
        from rugbrain.ingest_sources.x_intel.budget import BudgetTracker

        try:
            use_gx = await BudgetTracker().can_afford_call()
        except Exception as exc:
            logger.debug("xintel:budget_check_failed err=%s", str(exc)[:120])
            use_gx = True
    if not use_gx:
        logger.info("xintel:lane=pry (budget/pacing) handles=%d", len(batch))
        return [], list(batch)
    gx = [h for h in batch if route_account(h) == "getxapi"]
    pry = [h for h in batch if route_account(h) == "pry"]
    return gx, pry


async def _dedupe(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe + reshape tweets into embeddable docs (Redis seen-set, fail-open)."""
    from rugbrain.ingest_sources.x_intel.dedupe import tweets_to_docs

    return tweets_to_docs(await dedupe_seen(tweets))


async def run_cycle(
    select: SelectFn = _select_accounts, pry_fetch: FetchPryFn = _fetch_pry_tweets
) -> dict[str, Any]:
    """One cycle. Returns a counts summary.

    Raises ``PryLaneUnavailable`` when the Pry lane is payment-gated (402) and
    scraped nothing; anything else degrades to zero counts.
    """
    started = time.time()
    summary: dict[str, Any] = {
        "fetched": 0,
        "unique": 0,
        "ingested": 0,
        "getxapi_handles": 0,
        "pry_handles": 0,
    }
    pry_lane_dead = False
    try:
        gx, pry = await select()
        summary["getxapi_handles"] = len(gx)
        summary["pry_handles"] = len(pry)
        from rugbrain.ingest_sources.x_intel.config import TWEETS_PER_ACCOUNT

        tweets: list[dict[str, Any]] = []
        gx_tweets: list[dict[str, Any]] = []
        if gx:
            gx_tweets = await _fetch_getxapi_tweets(gx, TWEETS_PER_ACCOUNT)
            tweets.extend(gx_tweets)
        # Behavioral failover: a paid lane that returns NOTHING for a
        # non-empty batch is dead (plan credits expired 2026-10-02) — hand
        # its handles to the free Pry lane instead of ending the cycle with
        # fetched=0. The budget ledger alone cannot catch this: an expired
        # plan fails calls without ever charging the counters.
        if gx and not gx_tweets:
            logger.warning("xintel:getxapi_empty handles=%d -> pry failover", len(gx))
            pry = list(pry) + list(gx)
        if pry:
            pry_tweets, pry_lane_dead = await pry_fetch(pry)
            tweets.extend(pry_tweets)
        summary["fetched"] = len(tweets)
        docs = await _dedupe(tweets)
        summary["unique"] = len(docs)
        if docs:
            summary["ingested"] = await store_docs(docs)
    except Exception as exc:
        logger.warning("xintel:cycle_failed err=%s", str(exc)[:200])
        summary["error"] = str(exc)[:200]
    summary["seconds"] = round(time.time() - started, 1)
    return await _finalize(summary, pry_lane_dead)


async def _finalize(summary: dict[str, Any], pry_lane_dead: bool) -> dict[str, Any]:
    """Log the cycle, mark a dead Pry lane, and raise so the runner exits nonzero."""
    if pry_lane_dead:
        summary["pry_lane_dead"] = True
        logger.error(
            "xintel:pry_lane_dead pry_handles=%s fetched=%s action=check_PRY_API_KEY_and_x402_credits",
            summary["pry_handles"],
            summary["fetched"],
        )
    logger.info(
        "xintel_cycle_done fetched=%s unique=%s ingested=%s",
        summary["fetched"],
        summary["unique"],
        summary["ingested"],
    )
    if pry_lane_dead:
        raise PryLaneUnavailable(
            f"pry lane payment-gated (402) for all {summary['pry_handles']} handles"
        )
    return summary


__all__ = ["run_cycle"]
