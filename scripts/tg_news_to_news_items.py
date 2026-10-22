#!/usr/bin/env python3
"""Mirror Telegram public-preview posts (JSONL) into Postgres ``news_items``.

The ``tg_news`` scraper writes append-only JSONL; the DataBus cache reads it but
nothing persisted those posts in the news table, so a channel could be ingested
and still invisible to ranking/RAG. This bridge closes that gap for one channel
at a time, reusing the *same* row shape and classifier as the RSS ingest:

  * ``source``       = channel handle (``glassnode``);
  * ``category``      = ``rugbrain.rss_classify.classify`` (deterministic);
  * ``source_trust``  = the channel's declared trust when given, else the
    classifier's source weight;
  * ``content_hash``  = ``rugbrain.rss_store.content_hash`` — the UNIQUE partial
    index collapses a repost instead of duplicating it.

Idempotent: existing ``news_id`` or ``content_hash`` rows are skipped.

Usage:
    python3 scripts/tg_news_to_news_items.py --channel glassnode --limit 100
    python3 scripts/tg_news_to_news_items.py --channel glassnode --trust 0.9 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.rss_classify import classify, trust_for  # noqa: E402
from rugbrain.rss_store import content_hash, news_id_for  # noqa: E402

TG_ROOT = Path("/srv/work/repos/rmi-backend/data/telegram_news")
INSERT = """
INSERT INTO news_items (news_id, url, title, summary, body_markdown, source,
                        published_at, ingested_at, chains_mentioned, tokens_mentioned,
                        sentiment_score, category, source_trust, content_hash)
VALUES ($1,$2,$3,$4,NULL,$5,$6,now(),ARRAY[]::TEXT[],ARRAY[]::TEXT[],NULL,$7,$8,$9)
ON CONFLICT DO NOTHING
"""


def _headline(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:140]
    return "(media post)"


def _published(post: dict) -> datetime:
    try:
        return datetime.fromisoformat(str(post.get("timestamp")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _rows(posts: list[dict], channel: str, trust: float | None) -> list[tuple]:
    rows = []
    for post in posts:
        text = str(post.get("text") or "").strip()
        if not text:
            continue
        title = _headline(text)
        url = post.get("url") or f"https://t.me/{channel}/{post.get('message_id')}"
        category, source_trust = classify(title, text[:2000], channel)
        rows.append(
            (
                news_id_for(url),
                url[:2048],
                title,
                text[:2000],
                channel,
                _published(post),
                category,
                trust if trust is not None else source_trust,
                content_hash(title, str(post.get("message_id") or ""), url),
            )
        )
    return rows


def _load(channel: str, limit: int, root: Path) -> list[dict]:
    path = root / f"{channel}.jsonl"
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows[-limit:]


async def mirror(channel: str, limit: int, trust: float | None, root: Path, dry_run: bool) -> dict:
    posts = _load(channel, limit, root)
    rows = _rows(posts, channel, trust)
    stats = {
        "channel": channel,
        "posts_read": len(posts),
        "rows_built": len(rows),
        "inserted": 0,
        "duplicate": 0,
        "dry_run": dry_run,
    }
    if dry_run:
        stats["sample"] = rows[:2]
        return stats
    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
    )
    try:
        for row in rows:
            if await conn.fetchval(
                "SELECT 1 FROM news_items WHERE news_id=$1 OR content_hash=$2 LIMIT 1",
                row[0],
                row[8],
            ):
                stats["duplicate"] += 1
                continue
            await conn.execute(INSERT, *row)
            stats["inserted"] += 1
    finally:
        await conn.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--trust", type=float, default=None, help="declared channel trust (0-1)")
    ap.add_argument("--root", type=Path, default=TG_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(
        json.dumps(
            asyncio.run(mirror(args.channel, args.limit, args.trust, args.root, args.dry_run)),
            indent=1,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
