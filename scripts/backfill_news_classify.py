#!/usr/bin/env python3
"""Backfill legacy ``news_items`` rows with the deterministic classifier.

Every row written before 2026-10-22 carries ``category`` / ``source_trust`` /
``content_hash`` as NULL. This script labels them with exactly the classifier the
live ingest uses (``rugbrain.rss_classify.classify``) — no LLM, no network — and
derives ``content_hash`` with the same function (``rugbrain.rss_store``).

Guarantees:
  * never overwrites a non-NULL column;
  * a row whose title AND summary are both blank is left NULL (nothing to label);
  * a hash already claimed by another row is left NULL rather than forced into the
    UNIQUE partial index (the duplicate story stays visible, not silently merged);
  * ``--dry-run`` reports without writing.

Usage:
    python3 scripts/backfill_news_classify.py --dry-run
    python3 scripts/backfill_news_classify.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter

import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugbrain.rss_classify import classify  # noqa: E402
from rugbrain.rss_store import content_hash  # noqa: E402

SELECT = """
SELECT news_id, title, summary, source, url, category, source_trust, content_hash
FROM news_items
WHERE category IS NULL OR source_trust IS NULL OR content_hash IS NULL
ORDER BY published_at
"""
UPDATE = """
UPDATE news_items SET
  category     = COALESCE(category, $2),
  source_trust = COALESCE(source_trust, $3),
  content_hash = COALESCE(content_hash, $4)
WHERE news_id = $1
"""


def _label(row: asyncpg.Record, taken: set[str]) -> tuple[str | None, float | None, str | None]:
    title = (row["title"] or "").strip()
    summary = (row["summary"] or "").strip()
    if not title and not summary:
        return None, None, None  # nothing to label — never guess
    category, trust = classify(title, summary, row["source"] or "")
    digest = row["content_hash"]
    if not digest:
        candidate = content_hash(title, "", row["url"] or "")
        digest = None if candidate in taken else candidate
        if digest:
            taken.add(digest)
    return category, trust, digest


async def backfill(dry_run: bool) -> dict[str, object]:
    conn = await asyncpg.connect(
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", "rmi"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        database=os.getenv("POSTGRES_DB", "rmi"),
    )
    try:
        taken = {
            row["content_hash"]
            for row in await conn.fetch(
                "SELECT content_hash FROM news_items WHERE content_hash IS NOT NULL"
            )
        }
        rows = await conn.fetch(SELECT)
        updates = []
        unlabelable = 0
        hash_collisions = 0
        categories: Counter[str] = Counter()
        trusts: Counter[str] = Counter()
        for row in rows:
            category, trust, digest = _label(row, taken)
            if category is None:
                unlabelable += 1
                continue
            if digest is None and row["content_hash"] is None:
                hash_collisions += 1
            updates.append((row["news_id"], category, trust, digest))
            categories[category] += 1
            if trust is not None:
                trusts[f"{trust:.2f}"] += 1
        if updates and not dry_run:
            await conn.executemany(UPDATE, updates)
        remaining = await conn.fetchval("SELECT count(*) FROM news_items WHERE category IS NULL")
        total = await conn.fetchval("SELECT count(*) FROM news_items")
        return {
            "candidates": len(rows),
            "updated": 0 if dry_run else len(updates),
            "dry_run": dry_run,
            "unlabelable_left_null": unlabelable,
            "content_hash_collisions_left_null": hash_collisions,
            "category_distribution": dict(categories.most_common()),
            "source_trust_distribution": dict(sorted(trusts.items())),
            "category_null_after": remaining,
            "rows_total": total,
        }
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(backfill(args.dry_run)), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
