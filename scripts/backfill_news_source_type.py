#!/usr/bin/env python3
"""Backfill ``news_items.source_type`` from each row's stored provenance.

Every ingest lane wrote its row without ``source_type`` — only the X lane
(``xsession/store.py``) and the 2026-10-22 Telegram/X smoke runs set it. This
labels the remaining rows from what the row already carries, never from a
guess about content:

  * ``x``        — ``news_id`` ``x:…`` or an x.com / twitter.com URL (the X lane).
  * ``telegram`` — ``news_id`` ``tg:…``, a ``tg:<channel>`` / ``tg_preview``
                   ``body_source``, or a t.me URL (bot + alt-channel lanes).
  * ``web``      — ``news_id`` ``scraped:…``, or any id namespace that is not
                   the RSS/X/Telegram one (site scrapers, newsletters).
  * ``security`` — RSS row whose publisher is a dedicated security outlet
                   (publisher class, deterministic allow-list below).
  * ``rss``       — everything else in the ``cryptopanic:`` feed namespace.

Precedence is Telegram before X, so a channel post quoting an x.com link
stays ``telegram``. Existing non-NULL ``source_type`` is never overwritten. Nothing
is deleted. ``--dry-run`` prints the same counts without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter

import asyncpg

#: Dedicated security outlets inside the RSS namespace. General tech desks
#: (Wired, Ars Technica) stay ``rss`` — they are not a security feed.
SECURITY_SOURCES = frozenset(
    {
        "the hacker news",
        "bleepingcomputer",
        "krebs on security",
        "securityweek",
        "the record",
        "hackread",
        "darkreading",
        "slowmist",
        "peckshield",
        "certik",
        "rekt",
        "rekt.news",
        "immunefi",
    }
)

#: Values the classifier may write.
SOURCE_TYPES = ("telegram", "x", "rss", "security", "web")

FETCH = """
SELECT news_id, url, source, body_source, source_type
  FROM news_items
 WHERE source_type IS NULL
ORDER BY published_at
"""
UPDATE = "UPDATE news_items SET source_type = $2 WHERE news_id = $1"


def classify_row(row: asyncpg.Record) -> str:
    """One row in, one source_type out. Pure — no IO, no content guessing."""
    news_id = row["news_id"] or ""
    url = (row["url"] or "").lower()
    body_source = (row["body_source"] or "").lower()
    source = (row["source"] or "").strip().lower()
    prefix = news_id.split(":", 1)[0].lower()

    # Telegram first: a channel post that QUOTES an x.com link is still a
    # Telegram post (its news_id/body_source say so). Doing x first relabelled
    # those rows — caught 2026-10-23 on tg:69eaff… whose url was an x.com link.
    if (
        prefix == "tg"
        or body_source.startswith("tg:")
        or body_source == "tg_preview"
        or "t.me/" in url
    ):
        return "telegram"
    if prefix == "x" or url.startswith(("https://x.com/", "https://twitter.com/",
                                        "https://www.x.com/", "https://www.twitter.com/")):
        return "x"
    if prefix == "scraped" or prefix not in ("cryptopanic", "tg", "x"):
        return "web"
    if source in SECURITY_SOURCES:
        return "security"
    return "rss"


async def conndb() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
    )


async def run(dry_run: bool, limit: int) -> dict[str, object]:
    conn = await conndb()
    stats: dict[str, object] = {"scanned": 0, "would_write": 0, "written": 0,
                                "assigned": {}, "errors": 0, "dry_run": dry_run}
    try:
        rows = await conn.fetch(FETCH)
        if limit:
            rows = rows[:limit]
        ups: list[tuple[str, str]] = []
        assigned: Counter[str] = Counter()
        for row in rows:
            stats["scanned"] = int(stats["scanned"]) + 1
            try:
                label = classify_row(row)
            except (TypeError, AttributeError):
                stats["errors"] = int(stats["errors"]) + 1
                continue
            assigned[label] += 1
            ups.append((row["news_id"], label))
        stats["would_write"] = len(ups)
        stats["assigned"] = dict(assigned)
        if not dry_run and ups:
            await conn.executemany(UPDATE, ups)
            stats["written"] = len(ups)
    finally:
        await conn.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill news_items.source_type from provenance")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="cap rows (0 = all)")
    args = ap.parse_args()
    print(asyncio.run(run(args.dry_run, args.limit)))


if __name__ == "__main__":
    main()
