"""news_items persistence for the X lane.

Idempotency is the database's job: ``news_items_x_tweet_uniq`` is a partial
unique index on ``(tweet_id) WHERE source_type='x'`` and ``idx_news_content_hash``
is unique too, so a bare ``ON CONFLICT DO NOTHING`` covers both — a re-run
inserts zero rows and reports it honestly instead of silently upserting.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

import asyncpg

from rugbrain.xsession import config

INSERT = """
INSERT INTO news_items (news_id, url, title, summary, body_markdown, source,
                        published_at, source_type, tweet_id, source_trust,
                        content_hash, category)
VALUES ($1,$2,$3,$4,$5,$6,$7,'x',$8,$9,$10,$11)
ON CONFLICT DO NOTHING
RETURNING news_id
"""

MAX_BODY = 8000
TITLE_LEN = 180


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def content_hash(tweet_id: str) -> str:
    """Stable per-tweet hash — a repost under a second handle keeps its own id."""
    return hashlib.sha256(f"x:{tweet_id}".encode()).hexdigest()[:32]


def build_row(
    handle: str, tweet: dict[str, Any], category: str, trust: float
) -> tuple[Any, ...] | None:
    """Primary builder: one validated tweet → one news_items row (or None)."""
    published: datetime | None = tweet.get("created_at")
    text = (tweet.get("text") or "").strip()
    tweet_id = str(tweet.get("tweet_id") or "")
    if not tweet_id or not text or published is None:
        return None
    clean = _collapse(text)
    return (
        f"x:{tweet_id}",
        f"https://x.com/{handle}/status/{tweet_id}",
        clean[:TITLE_LEN],
        clean[:MAX_BODY],
        text[:MAX_BODY],
        f"@{handle}",
        published,
        tweet_id,
        float(trust),
        content_hash(tweet_id),
        category,
    )


async def upsert_tweets(
    conn: asyncpg.Connection,
    handle: str,
    tweets: list[dict[str, Any]],
    category: str,
    trust: float,
) -> dict[str, int]:
    """Primary writer: insert a handle's tweets, count inserted vs duplicate."""
    stats = {"fetched": len(tweets), "inserted": 0, "duplicate": 0, "skipped": 0}
    for tweet in tweets:
        row = build_row(handle, tweet, category, trust)
        if row is None:
            stats["skipped"] += 1
            continue
        try:
            inserted = await conn.fetchval(INSERT, *row)
        except asyncpg.PostgresError as exc:
            raise RuntimeError(f"insert failed for {row[0]}: {exc}") from exc
        if inserted:
            stats["inserted"] += 1
        else:
            stats["duplicate"] += 1
    return stats


async def count_x_rows(conn: asyncpg.Connection, handles: list[str] | None = None) -> dict[str, Any]:
    """Totals used by the run report (never guesses from the client side)."""
    total = await conn.fetchval(
        "SELECT count(*) FROM news_items WHERE source_type='x' AND tweet_id IS NOT NULL"
    )
    window = await conn.fetchval(
        "SELECT count(DISTINCT tweet_id) FROM news_items "
        "WHERE source_type='x' AND ingested_at > now() - interval '1 hour'"
    )
    by_handle: dict[str, int] = {}
    if handles:
        rows = await conn.fetch(
            "SELECT source, count(*) FROM news_items WHERE source_type='x' "
            "AND source = ANY($1::text[]) GROUP BY 1 ORDER BY 2 DESC",
            [f"@{h}" for h in handles],
        )
        by_handle = {r["source"]: r["count"] for r in rows}
    return {"total_x_rows": total, "last_hour_distinct": window, "by_handle": by_handle}
