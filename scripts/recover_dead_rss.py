#!/usr/bin/env python3
"""Resolve every errored RSS feed: recover it, or declare it dead with a reason.

Nothing may stay a silent 403/404/DNS row. ``config/dead_feeds.json`` is the
reviewed disposition list:

* ``recover`` — the site still publishes, so the feed is handed to the existing
  ``scrape_dead_sources.py`` machinery (sitemap -> FlareSolverr -> Pry listing)
  and its articles land in ``news_items`` like any RSS item;
* everything else that errors — marked ``dead_permanent`` in ``rss_feed_status``
  with the reason from ``dead_rules`` (chosen by http status / DNS failure), so
  the health table explains itself instead of repeating an unexplained error.

Rows recovered here carry no classification until
``scripts/backfill_news_classify.py`` runs (same deterministic classifier).

Usage:
    python3 scripts/recover_dead_rss.py --dry-run
    python3 scripts/recover_dead_rss.py --max-per-source 5
    python3 scripts/recover_dead_rss.py --mark-dead-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import scrape_dead_sources as sds  # noqa: E402

CONFIG = Path(__file__).resolve().parents[1] / "config" / "dead_feeds.json"
ERRORS = "SELECT source, url, http_status, last_error FROM rss_feed_status WHERE status='error'"


def _reason(row: asyncpg.Record, rules: dict[str, str]) -> str:
    status = int(row["http_status"] or 0)
    error = str(row["last_error"] or "")
    if status == 0 or any(
        k in error for k in ("Name or service", "No address", "Temporary failure")
    ):
        return rules["dns"]
    return rules.get(str(status), rules["default"])


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
    )


async def resolve(limit: int, dry_run: bool, mark_dead_only: bool) -> dict:
    cfg = json.loads(CONFIG.read_text())
    rules = cfg["dead_rules"]
    recover = {row["source"]: row for row in cfg["recover"]}
    conn = await _connect()
    stats = {
        "errors_seen": 0,
        "recover_attempted": 0,
        "recovered_new": 0,
        "recover_errors": 0,
        "marked_dead": 0,
        "dead_by_reason": {},
        "recovered": {},
    }
    try:
        errors = await conn.fetch(ERRORS)
        stats["errors_seen"] = len(errors)
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for row in errors:
                source = row["source"]
                if source in recover and not mark_dead_only:
                    stats["recover_attempted"] += 1
                    result = await sds._ingest_source(
                        client, conn, source, recover[source]["url"], limit, dry_run
                    )
                    stats["recovered_new"] += result["new"]
                    stats["recover_errors"] += result["errors"]
                    stats["recovered"][source] = {"found": result["found"], "new": result["new"]}
                    if result["found"]:
                        continue  # recovered: leave the row for the next RSS probe
                reason = _reason(row, rules)
                stats["dead_by_reason"][reason] = stats["dead_by_reason"].get(reason, 0) + 1
                stats["marked_dead"] += 1
                if not dry_run:
                    await conn.execute(
                        "UPDATE rss_feed_status SET status='dead_permanent', last_error=$2,"
                        " last_error_at=now(), updated_at=now() WHERE source=$1",
                        source,
                        reason,
                    )
    finally:
        await conn.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-per-source", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mark-dead-only", action="store_true")
    args = ap.parse_args()
    stats = asyncio.run(resolve(args.max_per_source, args.dry_run, args.mark_dead_only))
    print(json.dumps(stats, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
