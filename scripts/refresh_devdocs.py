#!/usr/bin/env python3
"""Bounded refresh for the devdocs: fetch, count, report. Never store, never embed.

Two jobs, deliberately separated. This script FETCHES and reports; storing is
``scripts/ingest_all_sources.py --source <name>``, which owns chunk → embed → Qdrant and
the 4096 dimension gate. A refresh script that also wrote vectors would be a second,
divergent ingest path — exactly what ``docs/ADDING-A-SOURCE.md`` forbids ("sources are
additive — never touch chunking, search or Qdrant").

WHY IT REPORTS FAILURES PER SOURCE
    A manifest of 10 feeds that emits 4 documents looks identical to a healthy run that
    happened to be throttled, unless the failures are counted and named. The exit code is
    non-zero only when NOTHING was fetched — a partial run is a fact to report, not a
    crash.

Usage:
    python scripts/refresh_devdocs.py                      # both groups, default caps
    python scripts/refresh_devdocs.py --limit 12           # bound the whole run
    python scripts/refresh_devdocs.py --group static       # static | feeds | all
    python scripts/refresh_devdocs.py --json               # machine-readable report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from rugbrain.ingest_sources.base import USER_AGENT, proxy_url
from rugbrain.ingest_sources.devdocs import collect
from rugbrain.ingest_sources.eng_blogs import collect_feeds

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

DEFAULT_LIMIT = 40


async def run(group: str, limit: int | None) -> dict[str, Any]:
    """Fetch the requested group(s) and return a combined report."""
    report: dict[str, Any] = {"group": group, "limit": limit}
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        proxy=proxy_url(),
    ) as client:
        if group in ("static", "all"):
            docs, stats = await collect(client, limit=limit)
            report["static"] = {**stats, "sample_titles": [d.title for d in docs[:5]]}
        if group in ("feeds", "all"):
            docs, stats = await collect_feeds(client, limit=limit)
            report["feeds"] = {**stats, "sample_titles": [d.title for d in docs[:5]]}
    return report


def _print_report(report: dict[str, Any]) -> None:
    """Print one human-readable block per group, failures included."""
    for key in ("static", "feeds"):
        section = report.get(key)
        if not section:
            continue
        print(f"\n== {key} ==")
        print(
            f"requested={section.get('requested', 0)} emitted={section.get('emitted', 0)} "
            f"skipped_thin={section.get('skipped_thin', 0)} "
            f"duplicates={section.get('duplicates', 0)} failed={section.get('failed', 0)}"
        )
        for name, per in sorted((section.get("by_source") or {}).items()):
            print(f"  {name:<24} {per}")
        for failure in section.get("failures") or []:
            print(f"  FAILED {failure}")
        titles = section.get("sample_titles") or []
        if titles:
            print(f"  sample: {titles}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("static", "feeds", "all"), default="all")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args()

    report = asyncio.run(run(args.group, args.limit if args.limit > 0 else None))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    emitted = sum(
        int((report.get(key) or {}).get("emitted", 0) or 0) for key in ("static", "feeds")
    )
    return 0 if emitted else 1


if __name__ == "__main__":
    raise SystemExit(main())
