#!/usr/bin/env python3
"""Multi-feed RSS/Atom ingest → Postgres ``news_items`` (one cron, one config).

What this fixes relative to the single-feed path:

* **Per-feed isolation** — a dead/403/slow feed becomes a row in
  ``rss_feed_status``, never a stalled batch. 12 concurrent GETs, 20 s each.
* **Conditional GET** — ETag / Last-Modified replayed (cache:
  ``data/rss_feed_validators.json``), so an unchanged feed costs a 304 and
  zero parsing.
* **Content-level dedupe** — see ``rugbrain.rss_store``.
* **Deterministic scoring** — every row gets ``category`` and ``source_trust``
  (``rugbrain.rss_classify``), so rugbrain can rank without an LLM per item.

Feeds: ``--feeds-file`` (data/rss_feeds.txt, one "URL [Name]" per line).

Usage:
    python scripts/rss_ingest_feeds.py --feeds-file data/rss_feeds.txt --days 7
    python scripts/rss_ingest_feeds.py --feeds-file data/rss_feeds.txt --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx

from rugbrain.rss_feed import fetch_feed, read_feeds_file
from rugbrain.rss_feed_status import (
    conditional_headers,
    ensure_status_table,
    load_validators,
    save_validators,
)
from rugbrain.rss_store import store_feed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("rss_ingest_feeds")

CONCURRENCY = 12
TIMEOUT = 20.0


async def run(
    feeds_file: str, days: int | None, dry_run: bool, limit: int | None
) -> dict[str, Any]:
    feeds = read_feeds_file(feeds_file)
    if limit:
        feeds = feeds[:limit]
    totals: dict[str, Any] = {
        "feeds": len(feeds),
        "ok": 0,
        "not_modified": 0,
        "failed": 0,
        "items": 0,
        "new": 0,
        "duplicate": 0,
        "dry_run": dry_run,
    }
    pool: asyncpg.Pool | None = None
    if not dry_run:
        # A pool, not one connection: feeds run concurrently and asyncpg refuses
        # overlapping operations on the same connection.
        pool = await asyncpg.create_pool(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            user=os.getenv("POSTGRES_USER", "rmi"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            database=os.getenv("POSTGRES_DB", "rmi"),
            timeout=8,
            min_size=2,
            max_size=CONCURRENCY,
        )
        async with pool.acquire() as setup_conn:
            await ensure_status_table(setup_conn)

    cutoff = datetime.now(UTC) - timedelta(days=days) if days else None
    validators = load_validators()
    sem = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY)

    async def _one(client: httpx.AsyncClient, url: str, name: str) -> dict[str, Any]:
        async with sem:
            if dry_run:
                rec = await fetch_feed(client, url, conditional_headers(validators, url))
                return {
                    "items": len(rec["items"]),
                    "new": len(rec["items"]),
                    "dup": 0,
                    "error": rec["error"],
                    "not_modified": rec["not_modified"],
                }
            assert pool is not None
            async with pool.acquire() as conn:
                return await store_feed(conn, validators, url, name, cutoff, client)

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, follow_redirects=True, limits=limits
        ) as client:
            results = await asyncio.gather(
                *(_one(client, url, name) for url, name in feeds), return_exceptions=True
            )
    finally:
        if pool is not None:
            await pool.close()
    save_validators(validators)

    for (_url, name), result in zip(feeds, results, strict=True):
        if isinstance(result, BaseException):
            totals["failed"] += 1
            log.warning("feed_exception source=%s err=%s", name, str(result)[:160])
            continue
        totals["items"] += result["items"]
        totals["new"] += result["new"]
        totals["duplicate"] += result["dup"]
        if result["not_modified"]:
            totals["not_modified"] += 1
        elif result["error"]:
            totals["failed"] += 1
        else:
            totals["ok"] += 1
    log.info("rss_ingest_done %s", json.dumps(totals, default=str))
    return totals


def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-feed RSS ingest into news_items")
    ap.add_argument("--feeds-file", default="config/rss_feeds.txt")
    ap.add_argument(
        "--days", type=int, default=7, help="only items newer than N days (0 = no cutoff)"
    )
    ap.add_argument("--limit", type=int, default=None, help="only the first N feeds")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    stats = asyncio.run(run(args.feeds_file, args.days or None, args.dry_run, args.limit))
    print(json.dumps(stats, indent=1, default=str))


if __name__ == "__main__":
    main()
