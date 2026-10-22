#!/usr/bin/env python3
"""Split errored feeds into permanent-dead (config-documented) vs blocked.

A 403/429 is not the same failure as an NXDOMAIN: the first needs a bypass, the
second will never work again. This pass makes that distinction durable:

* **permanent dead** (DNS / 401 / 404 / 410) — removed from
  ``config/rss_feeds.txt`` and appended to ``config/rss_feeds_dead.txt`` with the
  reason, so the cron stops retrying them and no error stays unexplained;
* **blocked** (403 / 429 / 500 / 202 / non-XML / no entries) — kept in the live
  config, ``rss_feed_status`` annotated with why it is blocked and what unblocks
  it (FlareSolverr/Pry bypass), so the next probe retries with context;
* **recover** — the curated primary sources go through ``recover_dead_rss.py``
  (sitemap path) and stay in the live config.

Idempotent: rows already dead are skipped; the dead file is rewritten sorted.

Usage:
    python3 scripts/prune_dead_feeds.py --dry-run
    python3 scripts/prune_dead_feeds.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "config" / "rss_feeds.txt"
DEAD = ROOT / "config" / "rss_feeds_dead.txt"
DISPOSITIONS = ROOT / "config" / "dead_feeds.json"
DNS_MARKERS = ("Name or service", "No address", "Temporary failure")
PERMANENT = {401, 404, 410}
BLOCKED = {202, 403, 429, 500}


def _read_feeds(path: Path) -> list[tuple[str, str]]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        url, _, name = line.partition("\t")
        rows.append((url.strip(), name.strip() or url.strip()))
    return rows


def _classify(status: int, error: str) -> tuple[str, str]:
    if status == 0 or any(marker in error for marker in DNS_MARKERS):
        return "dead", "domain no longer resolves (DNS) — the site is gone"
    if status in PERMANENT:
        return (
            "dead",
            f"publisher retired the feed (HTTP {status}) and no article sitemap was found",
        )
    if status in BLOCKED or "not xml" in error or "no entries" in error:
        return "blocked", (
            f"blocked (HTTP {status or 'n/a'} / {error[:60] or 'unparseable'}) — needs the "
            "FlareSolverr/Pry bypass; FlareSolverr :8192 returned 500 on 2026-10-23"
        )
    return "blocked", f"unclassified failure ({error[:80]}) — retried with context"


async def prune(dry_run: bool) -> dict:
    cfg = json.loads(DISPOSITIONS.read_text())
    recover = {row["source"] for row in cfg["recover"]}
    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
    )
    stats = {"dead": 0, "blocked": 0, "kept_recover": 0, "live_before": 0, "live_after": 0}
    dead_rows: list[tuple[str, str, str]] = []
    try:
        rows = await conn.fetch(
            "SELECT source, url, http_status, last_error, status FROM rss_feed_status"
            " WHERE status IN ('error','dead_permanent')"
        )
        dead_urls = {row["url"] for row in rows if row["source"] not in recover}
        for row in rows:
            kind, reason = _classify(int(row["http_status"] or 0), str(row["last_error"] or ""))
            if row["source"] in recover:
                stats["kept_recover"] += 1
                continue
            if kind == "dead" or row["status"] == "dead_permanent":
                dead_rows.append((row["url"], row["source"], reason))
                stats["dead"] += 1
            else:
                stats["blocked"] += 1
            if not dry_run:
                await conn.execute(
                    "UPDATE rss_feed_status SET status=$2, last_error=$3, last_error_at=now(),"
                    " updated_at=now() WHERE source=$1",
                    row["source"],
                    "dead_permanent" if kind == "dead" else "error",
                    ("permanently dead: " if kind == "dead" else "") + reason,
                )
        feeds = _read_feeds(LIVE)
        stats["live_before"] = len(feeds)
        kept = [(url, name) for url, name in feeds if url not in dead_urls]
        stats["live_after"] = len(kept)
        if not dry_run:
            LIVE.write_text("".join(f"{url}\t{name}\n" for url, name in kept))
            stamp = datetime.now(UTC).strftime("%Y-%m-%d")
            header = (
                f"# permanently dead feeds — pruned {stamp}\n"
                "# URL\\tName\\tReason   (kept for audit; do not re-add without a new site)\n"
            )
            DEAD.write_text(header + "".join(f"{u}\t{n}\t{r}\n" for u, n, r in sorted(dead_rows)))
        stats["reasons"] = dict(Counter(reason.split(" —")[0] for _, _, reason in dead_rows))
    finally:
        await conn.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(asyncio.run(prune(args.dry_run)), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
