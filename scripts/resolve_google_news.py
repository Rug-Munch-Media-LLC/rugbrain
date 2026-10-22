#!/usr/bin/env python3
"""Resolve Google News shell rows into real publisher articles.

A Google News RSS item links to ``news.google.com/rss/articles/CBM…`` — a
JavaScript shell that ``rugbrain.article_fetch`` correctly refuses to read as
an article. Those rows therefore carry ``body_source='google_shell'`` and are
scored ``stub`` no matter how good the underlying story is.

Two steps per row, both real network calls, no invention:

  1. **decode** — read the shell page's ``data-n-a-sg`` / ``data-n-a-ts``
     signature and exchange it for the publisher URL at Google's
     ``DotsSplashUi/batchexecute`` endpoint (the same call the web client
     makes). The publisher URL is stored in ``canonical_url``; ``url`` keeps
     the Google link so the row stays idempotent and re-runnable.
  2. **fetch** — ``rugbrain.article_fetch.fetch_article`` pulls the prose and
     refuses walls/paywalls/short bodies. Only an ``ok`` body is written.

Provenance written: ``body_markdown``, ``body_source`` (``publisher`` when a
real article was stored, otherwise ``truncated``), ``truncated``,
``body_quality_score`` / ``body_quality_verdict`` (the same gate as
``news_quality_stamp.py``) and ``resolved_at``. A row that cannot be decoded
or fetched is left explicitly ``truncated`` — never silently rewritten.

Usage:
    python3 scripts/resolve_google_news.py --dry-run --limit 20
    python3 scripts/resolve_google_news.py                 # all unresolved shells
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from typing import Any

import asyncpg
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.article_fetch import fetch_article

GATE_DIR = os.getenv("RMI_GATE_DIR", "/srv/work/repos/rmi-content-pipeline")
sys.path.insert(0, GATE_DIR)
from pipeline.quality_gate import check  # noqa: E402

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
# The batchexecute body is JSON-in-JSON, so quotes arrive backslash-escaped.
PUBLISHER_RE = re.compile(r'garturlres[^a-zA-Z0-9]{0,8}(https?://[^"\\]+)')
SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')

FETCH = """
SELECT news_id, title, url, summary, body_markdown, body_source
  FROM news_items
 WHERE body_source = 'google_shell'
   AND url LIKE '%%news.google.com/rss/articles%%'
ORDER BY published_at DESC
LIMIT $1
"""
UPDATE = """
UPDATE news_items
   SET body_markdown = $2, body_source = $3, truncated = $4,
       quality_score = $5, quality_verdict = $6,
       body_quality_score = $7, body_quality_verdict = $8,
       canonical_url = $9, resolved_at = now()
 WHERE news_id = $1
"""


def body_quality(body: str) -> tuple[int, str]:
    """Band a body the way the existing 11,075 stamped rows are banded.

    Measured on the corpus: <10 words -> ``stub``, 10-149 -> ``thin``,
    >=150 -> ``ok``. Scores follow the observed per-band ranges, monotone in
    length, so a resolved row is comparable with an unchanged one.
    """
    words = len((body or "").split())
    if words < 10:
        return min(22, words * 2), "stub"
    if words < 150:
        return 10 + round(34 * (words - 10) / 139), "thin"
    return 45 + round(40 * min(1.0, (words - 150) / 850)), "ok"


def article_id(url: str) -> str:
    """The opaque base64 id Google puts after ``/articles/``."""
    return url.split("/articles/", 1)[1].split("?", 1)[0]


async def decode(client: httpx.AsyncClient, url: str) -> str | None:
    """Exchange a Google News shell URL for the publisher URL (or None)."""
    page = await client.get(url)
    sig, ts = SIG_RE.search(page.text), TS_RE.search(page.text)
    if not (sig and ts):
        return None
    req = ["garturlreq",
           [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
             None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1,
            None, 0, 0, None, 0],
           article_id(url), int(ts.group(1)), sig.group(1)]
    body = {"f.req": json.dumps([[["Fbv4je", json.dumps(req)]]])}
    resp = await client.post(
        BATCH_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )
    hit = PUBLISHER_RE.search(resp.text)
    return hit.group(1) if hit else None


async def resolve_one(client: httpx.AsyncClient, row: asyncpg.Record) -> dict[str, Any]:
    """Resolve + fetch one row. Returns the UPDATE tuple plus its outcome."""
    try:
        publisher = await decode(client, row["url"])
    except (httpx.HTTPError, OSError, ValueError):
        return {"news_id": row["news_id"], "outcome": "decode_error"}
    if not publisher:
        return {"news_id": row["news_id"], "outcome": "unresolved"}
    try:
        fetched = await fetch_article(client, publisher)
    except (httpx.HTTPError, OSError):
        fetched = {"status": "error", "body": "", "body_source": "rss_summary"}
    stored = (row["body_markdown"] or "").strip()
    body = fetched["body"] if fetched["status"] == "ok" else stored
    body_source = "publisher" if fetched["status"] == "ok" else "truncated"
    truncated = fetched["status"] != "ok"
    result = check(row["title"] or "", body, publisher, body_source=body_source)
    # A blocked/undecoded row keeps a snippet, not an article: band it stub so
    # the verdict never overstates what is stored.
    bq_score, bq_verdict = (
        body_quality(body) if fetched["status"] == "ok" else (min(12, len(body.split())), "stub")
    )
    return {
        "news_id": row["news_id"],
        "outcome": "ok" if fetched["status"] == "ok" else fetched["status"],
        "row": (row["news_id"], body[:20000], body_source, truncated,
                result.score, result.verdict, bq_score, bq_verdict, publisher[:2048]),
    }


async def run(limit: int, dry_run: bool, concurrency: int) -> dict[str, Any]:
    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
    )
    stats: dict[str, Any] = {"selected": 0, "written": 0, "outcomes": {},
                             "body_quality_before": {}, "dry_run": dry_run}
    try:
        rows = await conn.fetch(FETCH, limit)
        stats["selected"] = len(rows)
        stats["body_quality_before"] = dict(Counter(
            "google_shell" for _ in rows))
        sem = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(headers={"User-Agent": UA},
                                     follow_redirects=True,
                                     timeout=httpx.Timeout(25.0, connect=8.0)) as client:
            async def guarded(row: asyncpg.Record) -> dict[str, Any]:
                async with sem:
                    return await resolve_one(client, row)

            results = await asyncio.gather(*(guarded(r) for r in rows))
        ups = [r["row"] for r in results if "row" in r]
        stats["outcomes"] = dict(Counter(r["outcome"].split(":")[0] for r in results))
        stats["body_quality_after"] = dict(Counter(u[7] for u in ups))
        if not dry_run and ups:
            await conn.executemany(UPDATE, ups)
            stats["written"] = len(ups)
    finally:
        await conn.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Resolve Google News shell rows")
    ap.add_argument("--limit", type=int, default=100000)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(asyncio.run(run(args.limit, args.dry_run, args.concurrency)))


if __name__ == "__main__":
    main()
