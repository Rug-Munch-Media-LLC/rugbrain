#!/usr/bin/env python3
"""Stamp news_items with body provenance + slop-gate verdicts. Never deletes.

Two modes:
  (default)  rows where quality_verdict IS NULL — the post-ingest hook, so
             every new row carries quality_score/quality_verdict.
  --backfill every row — re-derive body provenance from what is stored,
             strip page chrome from scraped bodies, re-score the corpus.

The gate itself lives in rmi-content-pipeline/pipeline/quality_gate.py
(one scorer, not two); this script only supplies rows and writes verdicts.

Usage:
    .venv/bin/python scripts/news_quality_stamp.py              # new rows only
    .venv/bin/python scripts/news_quality_stamp.py --backfill   # whole table
    .venv/bin/python scripts/news_quality_stamp.py --backfill --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import asyncpg

GATE_DIR = os.getenv("RMI_GATE_DIR", "/srv/work/repos/rmi-content-pipeline")
sys.path.insert(0, GATE_DIR)
from pipeline.quality_gate import check  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from rugbrain.rss_extract import MIN_ARTICLE_WORDS, clean_item  # noqa: E402

UPDATE = """
UPDATE news_items
   SET body_markdown=$2, body_source=$3, truncated=$4,
       quality_score=$5, quality_verdict=$6
 WHERE news_id=$1
"""
CHROME_HINT = ("oops, something went wrong", "skip to navigation", "skip to main content")
FETCH = """
SELECT news_id, title, url, summary, body_markdown, body_source
  FROM news_items
 WHERE ($1 OR quality_verdict IS NULL)
 ORDER BY published_at DESC
 LIMIT $2
"""


def stamp_row(row: asyncpg.Record) -> tuple[Any, ...] | None:
    """One row in, one UPDATE tuple out. Pure — no IO, no invention."""
    title = row["title"] or ""
    summary = row["summary"] or ""
    stored = row["body_markdown"] or ""
    source_hint = row["body_source"]
    item = {"title": title, "link": row["url"] or "", "description": summary}
    derived = clean_item(item)

    # Do not discard a real stored article; only replace chrome-led bodies.
    chrome_led = any(h in stored[:400].lower() for h in CHROME_HINT)
    kept = stored and not chrome_led and len(stored.split()) >= MIN_ARTICLE_WORDS
    if kept:  # never replace a real stored article with a shorter summary
        body, body_source, truncated = stored, source_hint or "article_html", False
    elif chrome_led:
        cleaned = clean_item(item | {"description": stored})
        body, body_source, truncated = cleaned["body"], cleaned["body_source"], cleaned["truncated"]
    else:
        body, body_source, truncated = derived["body"], derived["body_source"], derived["truncated"]

    if not body.strip():
        return None
    result = check(title, body, row["url"] or "", body_source=body_source)
    return (row["news_id"], body[:20000], body_source, truncated,
            result.score, result.verdict)


async def run(backfill: bool, limit: int, dry_run: bool) -> dict[str, Any]:
    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
    )
    stats: dict[str, Any] = {"scan": 0, "stamped": 0, "skipped": 0, "verdicts": {},
                             "body_sources": {}, "dry_run": dry_run}
    try:
        rows = await conn.fetch(FETCH, backfill, limit)
        ups: list[tuple[Any, ...]] = []
        for row in rows:
            stats["scan"] += 1
            out = stamp_row(row)
            if out is None:
                stats["skipped"] += 1
                continue
            ups.append(out)
            stats["verdicts"][out[5]] = stats["verdicts"].get(out[5], 0) + 1
            stats["body_sources"][out[2]] = stats["body_sources"].get(out[2], 0) + 1
        if not dry_run and ups:
            await conn.executemany(UPDATE, ups)
            stats["stamped"] = len(ups)
    finally:
        await conn.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Stamp news_items with body provenance + slop verdicts")
    ap.add_argument("--backfill", action="store_true", help="re-derive every row, not just unjudged")
    ap.add_argument("--limit", type=int, default=0, help="cap rows (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    stats = asyncio.run(run(args.backfill, args.limit or 10**9, args.dry_run))
    print(stats)


if __name__ == "__main__":
    main()
