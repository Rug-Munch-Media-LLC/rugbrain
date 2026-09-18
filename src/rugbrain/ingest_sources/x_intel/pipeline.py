"""X social-intel pipeline — fetch → normalize → dedupe → embed → news_v2.

One cycle (``--cycle``):
1. Fetch TIER 1/2 account timelines via GetXAPI (budget-gated, round-robin,
   at most ``MAX_ACCOUNTS_PER_CYCLE`` accounts per cycle).
2. Fetch TIER 3/4 long-tail content via keyword search (GetXAPI) with full
   tweet text scraped free via Pry when pacing/budget says so.
3. Normalize every tweet into the shared shape, dedupe in-batch by tweet id
   and cross-run via a Redis seen-set (fail-open).
4. Embed + upsert survivors into Qdrant ``news_v2`` via the shared
   ``rugbrain.ingest_common.store_documents`` machinery.

Fail-open: every stage degrades to zero counts; nothing raises.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("rugbrain.x_intel")


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
                    tweet["handle"] = handle
                    # GetXAPI's timeline payload omits the author, so the
                    # normalized URL is the generic /i/status/ form. Rebuild it
                    # with the known handle so the stored doc is attributable.
                    tid = str(tweet.get("tweet_id") or "").strip()
                    if tid:
                        tweet["url"] = f"https://x.com/{handle}/status/{tid}"
                out.append(tweet)
    return out


async def _fetch_pry_tweets(handles: list[str]) -> list[dict[str, Any]]:
    """Pull recent tweets for *handles* via free Pry profile scrapes."""
    from rugbrain.ingest_sources.x_intel.pry_lane import scrape_profile

    out: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(3)

    async def one(handle: str) -> None:
        async with sem:
            try:
                profile = await scrape_profile(handle)
            except Exception as exc:
                logger.debug("xintel:pry_profile_failed handle=%s err=%s", handle, str(exc)[:120])
                return
            if profile:
                out.extend(profile.get("recent_tweets") or [])

    await asyncio.gather(*(one(h) for h in handles))
    return out


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
    from rugbrain.ingest_sources.x_intel.routing import route_account

    tier12 = [
        h for h in list(TIER1_ACCOUNTS) + list(TIER2_ACCOUNTS) if route_account(h) == "getxapi"
    ]
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
    pry = [h for h in batch if route_account(h) == "pry"]
    gx = [h for h in batch if route_account(h) == "getxapi"]
    return gx, pry


async def _dedupe(tweets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """In-batch dedupe by tweet id + cross-run Redis seen-set (fail-open)."""
    from rugbrain.ingest_sources.x_intel.dedupe import dedupe_seen

    return await dedupe_seen(tweets)


async def run_cycle() -> dict[str, Any]:
    """One full ingestion cycle. Returns a counts summary (never raises)."""
    started = time.time()
    summary: dict[str, Any] = {
        "fetched": 0,
        "unique": 0,
        "ingested": 0,
        "getxapi_handles": 0,
        "pry_handles": 0,
    }
    try:
        gx, pry = await _select_accounts()
        summary["getxapi_handles"] = len(gx)
        summary["pry_handles"] = len(pry)
        from rugbrain.ingest_sources.x_intel.config import TWEETS_PER_ACCOUNT

        tweets: list[dict[str, Any]] = []
        if gx:
            tweets.extend(await _fetch_getxapi_tweets(gx, TWEETS_PER_ACCOUNT))
        if pry:
            tweets.extend(await _fetch_pry_tweets(pry))
        summary["fetched"] = len(tweets)
        fresh = await _dedupe(tweets)
        from rugbrain.ingest_sources.x_intel.dedupe import tweets_to_docs

        docs = tweets_to_docs(fresh)
        summary["unique"] = len(docs)
        if docs:
            from rugbrain.embeddings import get_embedding
            from rugbrain.ingest_common import CrawlDocument, store_documents
            from rugbrain.qdrant_store import get_qdrant_store

            crawl_docs = [
                CrawlDocument(
                    title=d["content"][:120],
                    content=d["content"],
                    url=d["metadata"].get("url", ""),
                    source=d["source"],
                    collection=d["collection"],
                    category="x_social",
                    published=d["metadata"].get("ts", ""),
                    severity=d["severity"],
                    metadata=d["metadata"],
                )
                for d in docs
            ]

            class _E:
                async def embed_one(self, text: str) -> list[float]:
                    return await get_embedding(text)

            res = await store_documents(crawl_docs, _E(), await get_qdrant_store())
            summary["ingested"] = res.get("inserted", 0)
    except Exception as exc:
        logger.warning("xintel:cycle_failed err=%s", str(exc)[:200])
        summary["error"] = str(exc)[:200]
    summary["seconds"] = round(time.time() - started, 1)
    logger.info(
        "xintel_cycle_done fetched=%s unique=%s ingested=%s",
        summary["fetched"],
        summary["unique"],
        summary["ingested"],
    )
    return summary


__all__ = ["run_cycle"]
