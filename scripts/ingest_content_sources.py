#!/usr/bin/env python3
"""Fetch every catalogued content source once and ingest what is new.

Bounded by construction: one conditional GET per source, a fixed concurrency
cap, and ``--limit`` entries per feed. ``--dry-run`` performs the fetches and the
mapping (so provenance and parsing are exercised) but never touches Qdrant.

Usage::

    python scripts/ingest_content_sources.py --dry-run --kinds news,youtube
    python scripts/ingest_content_sources.py --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.content_sources import (
    fetch_source,
    ingest_items,
    load_catalog,
    to_news_item,
)
from rugbrain.ingest_sources.content_sources.fetch import FETCH_TIMEOUT, USER_AGENT

logger = logging.getLogger("content_sources")

CONCURRENCY = 4


async def run(kinds: set[str], limit: int, *, dry_run: bool) -> dict[str, Any]:
    """Fetch the catalogue, map entries, and (unless dry-run) ingest them."""
    specs = [spec for spec in load_catalog() if not kinds or spec.kind in kinds]
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:

        async def fetch_one(spec: Any) -> tuple[Any, dict[str, Any]]:
            async with semaphore:
                return spec, await fetch_source(spec, client=client, limit=limit)

        for spec, record in await asyncio.gather(*(fetch_one(spec) for spec in specs)):
            sources.append(
                {
                    "source_id": spec.id,
                    "kind": spec.kind,
                    "http_status": record["http_status"],
                    "entries": len(record["entries"]),
                    "error": record["error"],
                }
            )
            items.extend(to_news_item(spec, entry) for entry in record["entries"])

    report: dict[str, Any] = {
        "sources": len(specs),
        "items": len(items),
        "source_status": sources,
    }
    if dry_run:
        report["dry_run"] = True
        return report
    report.update(await ingest_items(items))
    return report


def main() -> int:
    """CLI entry point; prints one JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kinds", default="", help="comma-separated kinds (default: all)")
    parser.add_argument("--limit", type=int, default=20, help="entries per source")
    parser.add_argument("--dry-run", action="store_true", help="fetch and map, do not store")
    args = parser.parse_args()
    kinds = {kind.strip() for kind in args.kinds.split(",") if kind.strip()}
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = asyncio.run(run(kinds, args.limit, dry_run=args.dry_run))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
