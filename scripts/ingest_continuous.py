#!/usr/bin/env python3
"""RugBrain continuous ingestion scheduler.

Runs every RugBrain ingestion pipeline on its own cadence:

    * every  5 min  — security alerts, market snapshot (+ fear & greed)
    * every 15 min  — news crawl, social intel
    * every  1 h    — codebase repos (GitHub audits / post-mortems / PoCs)
    * every  6 h    — full re-crawl of all sources

Checkpoint-based: each job's last successful run is persisted in Redis
(``rag:ingest:checkpoint:<job>``), falling back to an in-memory map when
Redis is unreachable. Restarts are safe — a job only runs when its interval
has elapsed since the last *successful* run, and failed runs do not advance
the checkpoint.

Idempotency: every store uses deterministic Qdrant point IDs, so re-running
a job upserts the same points instead of duplicating them.

Usage::

    python scripts/ingest_continuous.py                 # run as a service
    python scripts/ingest_continuous.py --once          # run due jobs, exit
    python scripts/ingest_continuous.py --job news_crawl --force
    python scripts/ingest_continuous.py --dry-run       # fetch, don't store
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("ingest_continuous")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

CHECKPOINT_PREFIX = "rag:ingest:checkpoint:"
TICK_SECONDS = int(os.getenv("INGEST_TICK_SECONDS", "60"))

# Cadences (seconds).
EVERY_5_MIN = 5 * 60
EVERY_15_MIN = 15 * 60
EVERY_1_HOUR = 60 * 60
EVERY_6_HOURS = 6 * 60 * 60


# ── Checkpoint store (Redis-backed, in-memory fallback) ─────────────────


class CheckpointStore:
    """Persist last-successful-run timestamps per job.

    Redis (``rag:ingest:checkpoint:<job>``) when reachable, otherwise an
    in-memory dict so the scheduler still works in minimal environments.
    """

    def __init__(self) -> None:
        self._redis: Any = None
        self._memory: dict[str, float] = {}
        self._redis_ok = False

    async def _client(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True
            )
            await r.ping()
            self._redis = r
            self._redis_ok = True
        except Exception as e:
            log.warning(
                "ingest_checkpoint_redis_unavailable err=%s — using in-memory state", str(e)[:100]
            )
            self._redis = False
        return self._redis if self._redis is not False else None

    async def last_run(self, job: str) -> float | None:
        client = await self._client()
        if client is not None:
            try:
                raw = await client.get(f"{CHECKPOINT_PREFIX}{job}")
                return float(raw) if raw else None
            except Exception:
                return self._memory.get(job)
        return self._memory.get(job)

    async def mark(self, job: str, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        self._memory[job] = ts
        client = await self._client()
        if client is not None:
            try:
                await client.set(f"{CHECKPOINT_PREFIX}{job}", str(ts))
            except Exception as e:
                log.debug("ingest_checkpoint_mark_failed job=%s err=%s", job, str(e)[:80])

    async def due(self, job: str, interval: int, now: float | None = None) -> bool:
        last = await self.last_run(job)
        if last is None:
            return True
        return (now if now is not None else time.time()) - last >= interval

    async def reset(self) -> None:
        self._memory.clear()
        client = await self._client()
        if client is not None:
            try:
                await client.flushdb()
            except Exception as e:
                log.debug("ingest_checkpoint_reset_failed err=%s", str(e)[:80])


# ── Job definitions ──────────────────────────────────────────────────────


async def _job_security_alerts(dry_run: bool) -> dict[str, Any]:
    """Poll crypto security sources (CoinGecko/DeFiLlama/Rekt/Nitter+SearXNG)."""
    import importlib

    alerts = importlib.import_module("rugbrain.alerts")

    result: dict[str, Any] = await alerts.poll_once(dry_run=dry_run)
    return {"source": "security_alerts", "result": result}


async def _job_market_snapshot(dry_run: bool) -> dict[str, Any]:
    """Build + store the at-a-glance market snapshot (includes fear & greed)."""
    from rugbrain.market_snapshot import MarketSnapshotStore, build_market_snapshot

    snapshot = await build_market_snapshot()
    stored = False
    if not dry_run:
        stored = MarketSnapshotStore().store(snapshot)
    return {"source": "market_snapshot", "snapshot_at": snapshot["generated_at"], "stored": stored}


async def _job_news_crawl(dry_run: bool) -> dict[str, Any]:
    """News feeds + additional data sources (RSS/API/crawl)."""
    from rugbrain.sources_registry import ingest_all_sources
    from rugbrain.web_crawler import WebCrawler, crawl_crypto_news

    c = WebCrawler()
    news = await crawl_crypto_news(c)
    sources = await ingest_all_sources(c, dry_run=dry_run)
    return {"source": "news_crawl", "news": news, "sources": sources}


async def _job_social_intel(dry_run: bool) -> dict[str, Any]:
    """X + Reddit + Farcaster social intelligence into Qdrant."""
    from rugbrain.social_intel import collect_social_intel

    _res: dict[str, Any] = await collect_social_intel(dry_run=dry_run)
    return _res


async def _job_codebase_repos(dry_run: bool) -> dict[str, Any]:
    """Check GitHub for new audit reports, post-mortems, PoCs."""
    from rugbrain.web_crawler import WebCrawler, crawl_github_audits

    result = await crawl_github_audits(WebCrawler())
    return {"source": "codebase_repos", "result": result}


async def _job_full_recrawl(dry_run: bool) -> dict[str, Any]:
    """Full source re-crawl every 6h: news + security + social + github + sources."""
    from rugbrain.sources_registry import ingest_all_sources
    from rugbrain.web_crawler import (
        WebCrawler,
        crawl_crypto_news,
        crawl_github_audits,
        crawl_security_feeds,
        crawl_social_sentiment,
    )

    c = WebCrawler()
    news = await crawl_crypto_news(c)
    security = await crawl_security_feeds(c)
    social = await crawl_social_sentiment(c)
    github = await crawl_github_audits(c)
    sources = await ingest_all_sources(c, dry_run=dry_run)
    return {
        "source": "full_recrawl",
        "news": news,
        "security": security,
        "social": social,
        "github": github,
        "sources": sources,
    }


@dataclass(frozen=True)
class Job:
    """A scheduled ingestion task."""

    name: str
    interval: int
    run: Callable[[bool], Awaitable[dict[str, Any]]]


JOBS: tuple[Job, ...] = (
    Job("security_alerts", EVERY_5_MIN, _job_security_alerts),
    Job("market_snapshot", EVERY_5_MIN, _job_market_snapshot),
    Job("news_crawl", EVERY_15_MIN, _job_news_crawl),
    Job("social_intel", EVERY_15_MIN, _job_social_intel),
    Job("codebase_repos", EVERY_1_HOUR, _job_codebase_repos),
    Job("full_recrawl", EVERY_6_HOURS, _job_full_recrawl),
)


def get_job(name: str) -> Job | None:
    for job in JOBS:
        if job.name == name:
            return job
    return None


# ── Scheduler core ───────────────────────────────────────────────────────


async def run_job(
    job: Job,
    checkpoints: CheckpointStore,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Run *job* if due (or forced). Returns the job's result summary.

    Marks a checkpoint only on success so failures are retried next tick.
    """
    now = time.time()
    if not force and not await checkpoints.due(job.name, job.interval, now):
        log.debug("ingest_skip job=%s (not due)", job.name)
        return {"job": job.name, "skipped": True}
    log.info("ingest_start job=%s dry_run=%s", job.name, dry_run)
    t0 = time.perf_counter()
    try:
        result = await job.run(dry_run)
    except Exception as e:
        log.exception("ingest_failed job=%s err=%s", job.name, str(e)[:200])
        return {"job": job.name, "ok": False, "error": str(e)[:200]}
    await checkpoints.mark(job.name, now)
    elapsed = round(time.perf_counter() - t0, 2)
    log.info(
        "ingest_done job=%s elapsed=%ss result=%s",
        job.name,
        elapsed,
        json.dumps(result, default=str)[:300],
    )
    return {"job": job.name, "ok": True, "elapsed": elapsed, "result": result}


async def run_due(
    checkpoints: CheckpointStore,
    *,
    dry_run: bool = False,
    force: bool = False,
    names: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Run every due (or forced) job in JOBS; returns per-job summaries."""
    targets = [j for j in JOBS if names is None or j.name in names]
    results: list[dict[str, Any]] = []
    for job in targets:
        results.append(await run_job(job, checkpoints, dry_run=dry_run, force=force))
    return results


async def run_forever(checkpoints: CheckpointStore, *, dry_run: bool = False) -> None:
    """Tick loop for service mode."""
    log.info("ingest_service_started tick=%ds jobs=%d", TICK_SECONDS, len(JOBS))
    while True:
        try:
            await run_due(checkpoints, dry_run=dry_run)
        except Exception as e:
            log.exception("ingest_tick_failed err=%s", str(e)[:200])
        await asyncio.sleep(TICK_SECONDS)


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="RugBrain continuous ingestion scheduler")
    parser.add_argument("--once", action="store_true", help="run due jobs once, then exit")
    parser.add_argument("--job", type=str, default=None, help="run only this job")
    parser.add_argument("--force", action="store_true", help="run regardless of checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="fetch only, don't store")
    parser.add_argument(
        "--reset-checkpoints", action="store_true", help="clear checkpoints and exit"
    )
    args = parser.parse_args()

    async def run() -> None:
        checkpoints = CheckpointStore()
        if args.reset_checkpoints:
            await checkpoints.reset()
            print("CHECKPOINTS RESET")
            return
        if args.job:
            job = get_job(args.job)
            if job is None:
                print(f"unknown job: {args.job}; available: {[j.name for j in JOBS]}")
                return
            result = await run_job(job, checkpoints, dry_run=args.dry_run, force=args.force)
            print(json.dumps(result, default=str))
            return
        if args.once:
            results = await run_due(checkpoints, dry_run=args.dry_run, force=args.force)
            print(json.dumps(results, default=str))
            return
        await run_forever(checkpoints, dry_run=args.dry_run)

    asyncio.run(run())


if __name__ == "__main__":
    main()
