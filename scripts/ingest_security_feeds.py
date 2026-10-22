#!/usr/bin/env python3
"""Ingest the security-source web feeds into ``news_items`` (trust 0.9).

Feeds live in ``config/security_feeds.txt`` — ``URL<TAB>Source<TAB>kind`` where
kind is ``rss`` (publisher feed/Atom) or ``sitemap`` (feed retired, article
sitemap live). Every path there was verified with a real request; sources whose
only channel is X are deliberately absent (the ``x_intel`` lane owns X).

Usage:
    python3 scripts/ingest_security_feeds.py --dry-run
    python3 scripts/ingest_security_feeds.py --only SlowMist --cap 20
    python3 scripts/ingest_security_feeds.py --json /tmp/sec_ingest.json
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

from rugbrain.sec_web import ingest_source

DEFAULT_FEEDS = Path(__file__).resolve().parents[1] / "config" / "security_feeds.txt"
CONCURRENCY = 4


def read_feeds(path: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        url, name = parts[0], parts[1].strip()
        out.append((url, name, (parts[2].strip().lower() if len(parts) > 2 else "rss")))
    return out


async def run(feeds: list[tuple[str, str, str]], cap: int, dry_run: bool) -> dict:
    pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
        timeout=8,
        min_size=1,
        max_size=CONCURRENCY,
    )
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(client: httpx.AsyncClient, url: str, name: str, kind: str) -> dict:
        async with sem, pool.acquire() as conn:
            if not dry_run:
                return await ingest_source(conn, client, name, url, kind, cap)
            tr = conn.transaction()
            await tr.start()
            try:
                return await ingest_source(conn, client, name, url, kind, cap)
            finally:
                await tr.rollback()

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            results = await asyncio.gather(
                *(one(client, u, n, k) for u, n, k in feeds), return_exceptions=True
            )
    finally:
        await pool.close()
    clean: list[dict] = []
    for (_url, name, kind), res in zip(feeds, results, strict=True):
        clean.append(
            res
            if isinstance(res, dict)
            else {
                "source": name,
                "kind": kind,
                "seen": 0,
                "new": 0,
                "duplicate": 0,
                "error": str(res)[:160],
            }
        )
    total = {
        "sources": len(clean),
        "ok": sum(1 for r in clean if not r["error"]),
        "seen": sum(r["seen"] for r in clean),
        "new": sum(r["new"] for r in clean),
        "duplicate": sum(r["duplicate"] for r in clean),
        "dry_run": dry_run,
    }
    return {"total": total, "per_source": clean}


def main() -> int:
    ap = argparse.ArgumentParser(description="security-source web ingest → news_items")
    ap.add_argument("--feeds-file", default=str(DEFAULT_FEEDS))
    ap.add_argument("--only", default="", help="substring filter on source name")
    ap.add_argument("--cap", type=int, default=20, help="newest N items per source")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", default="", help="write the per-source report here")
    args = ap.parse_args()
    feeds = read_feeds(Path(args.feeds_file))
    if args.only:
        feeds = [f for f in feeds if args.only.lower() in f[1].lower()]
    report = asyncio.run(run(feeds, args.cap, args.dry_run))
    text = json.dumps(report, indent=1, default=str)
    print(text)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
