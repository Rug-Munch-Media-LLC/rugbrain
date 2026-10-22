#!/usr/bin/env python3
"""Probe every configured RSS/Atom feed and write a machine-readable health report.

One job: for each feed, do a real conditional-GET probe and record

  status        HTTP status (0 = transport error/timeout)
  items         parsed entry count
  newest_age_h  hours since the newest item (None = no dates in feed)
  cadence_h     median gap between the 10 newest items, in hours
  etag / last_modified  validators the ingest replays as If-None-Match / If-Modified-Since
  fmt           rss | atom | json | unknown

Output (default: next to the feeds file):
  rss_feed_health.json   full record per feed, keyed by source name
  rss_feed_health.md     human table: source, status, items, newest, cadence, url

This is the evidence tool behind "which feeds are alive". ``--only-live`` prints
the subset safe to append to data/rss_feeds.txt.

Usage:
  python scripts/rss_feed_health.py --feeds-file data/rss_feeds.txt
  python scripts/rss_feed_health.py --feeds-file data/rss_feeds_candidates.txt --json /tmp/probe.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from rugbrain.rss_feed import fetch_feed, newest_epoch, parse_ts, read_feeds_file

TIMEOUT = 20.0
CONCURRENCY = 12
STALE_AFTER_H = 24 * 45


def cadence_h(entries: list[dict[str, Any]]) -> float | None:
    """Median gap (hours) between the 10 newest dated entries."""
    stamps = sorted((t for t in (parse_ts(e.get("date", "")) for e in entries) if t), reverse=True)[
        :11
    ]
    gaps = [(a - b) / 3600 for a, b in pairwise(stamps) if a > b]
    return round(statistics.median(gaps), 1) if gaps else None


async def probe(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str, name: str
) -> dict[str, Any]:
    async with sem:
        rec = await fetch_feed(client, url)
    entries = rec["items"]
    newest = newest_epoch(entries)
    return {
        "source": name,
        "url": url,
        "status": rec["http_status"],
        "items": len(entries),
        "newest_age_h": round((datetime.now(UTC).timestamp() - newest) / 3600, 1)
        if newest
        else None,
        "cadence_h": cadence_h(entries),
        "fmt": rec["fmt"],
        "etag": rec["etag"],
        "last_modified": rec["last_modified"],
        "error": rec["error"],
    }


def render_md(records: list[dict[str, Any]], generated: str) -> str:
    live = [r for r in records if r["status"] == 200 and r["items"] > 0]
    stale = [r for r in live if (r["newest_age_h"] or 0) > STALE_AFTER_H]
    lines = [
        "# RSS feed health",
        "",
        f"generated {generated} · probed {len(records)} · live {len(live)} · "
        f"stale>{STALE_AFTER_H}h {len(stale)} · dead {len(records) - len(live)}",
        "",
        "| source | status | items | newest_h | cadence_h | fmt | url |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(
        records, key=lambda x: (-(x["status"] == 200 and x["items"] > 0), x["source"].lower())
    ):
        state = r["status"] if r["status"] else r["error"]
        lines.append(
            f"| {r['source']} | {state} | {r['items']} | "
            f"{r['newest_age_h'] if r['newest_age_h'] is not None else '-'} | "
            f"{r['cadence_h'] if r['cadence_h'] is not None else '-'} | {r['fmt']} | {r['url']} |"
        )
    return "\n".join(lines) + "\n"


async def run(feeds: list[tuple[str, str]]) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY),
    ) as client:
        return list(await asyncio.gather(*(probe(client, sem, url, name) for url, name in feeds)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds-file", required=True)
    ap.add_argument("--json", default="")
    ap.add_argument(
        "--only-live", action="store_true", help="print appendable 'URL<TAB>Name' lines"
    )
    args = ap.parse_args()

    path = Path(args.feeds_file)
    records = asyncio.run(run(read_feeds_file(path)))
    if args.only_live:
        for r in records:
            if r["status"] == 200 and r["items"] > 0:
                print(f"{r['url']}\t{r['source']}")
        return 0

    out_json = Path(args.json) if args.json else path.parent / "rss_feed_health.json"
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    payload = {
        "generated": generated,
        "feeds_file": str(path),
        "counts": {
            "probed": len(records),
            "live": sum(1 for r in records if r["status"] == 200 and r["items"] > 0),
            "dead": sum(1 for r in records if r["status"] != 200 or r["items"] == 0),
        },
        "feeds": {r["source"]: r for r in records},
    }
    out_json.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    out_json.with_suffix(".md").write_text(render_md(records, generated), encoding="utf-8")
    print(json.dumps(payload["counts"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
