"""Security-source web intake into ``news_items`` (RSS first, sitemap second).

Closes the gap the Telegram *public-preview* lane cannot reach: channels whose
preview is closed (Lookonchain, MistTrack, ScamSniffer…) and research desks that
publish on their own sites. For every source this lane prefers the publisher's
own feed, then the article sitemap when the feed was retired — X is deliberately
NOT fetched here (the ``x_intel`` lane owns X, writes Qdrant, and pays per call).

Two row-level guarantees the generic RSS ingest does not give:

* ``source_trust`` is set to :data:`TRUST` (0.9) for this lane — a security
  firm's own advisory outranks an aggregator repost, whatever the classifier
  would otherwise assign;
* dedupe is checked on BOTH identities the table carries — ``news_id`` (URL
  hash) and ``content_hash`` (title + link) — so a re-run appends nothing and a
  story already mirrored from Telegram by URL is not stored twice.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from rugbrain.rss_classify import classify
from rugbrain.rss_extract import clean_item
from rugbrain.rss_feed import fetch_feed, parse_ts
from rugbrain.rss_feed_status import record_status
from rugbrain.sec_sitemap import article_text, sitemap_articles

TRUST = 0.9
INSERT = """
INSERT INTO news_items (news_id, url, title, summary, body_markdown, source,
                        published_at, ingested_at, chains_mentioned, tokens_mentioned,
                        sentiment_score, category, source_trust, content_hash,
                        body_source, truncated)
VALUES ($1,$2,$3,$4,$5,$6,$7,now(),ARRAY[]::TEXT[],ARRAY[]::TEXT[],NULL,$8,$9,$10,$11,$12)
ON CONFLICT DO NOTHING
"""


def _ids(link: str, title: str) -> tuple[str, str]:
    """``(news_id, content_hash)`` — the same shapes the RSS/TG lanes write."""
    news_id = f"cryptopanic:{hashlib.sha256(f'cryptopanic:{link}'.encode()).hexdigest()[:24]}"
    title_norm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", title.lower())).strip()
    chash = hashlib.sha256(f"{title_norm}|{link}".encode()).hexdigest()[:32]
    return news_id, chash


def _row(
    source: str,
    title: str,
    link: str,
    summary: str,
    body: str,
    published: datetime,
    body_source: str,
    truncated: bool,
) -> tuple[Any, ...]:
    news_id, chash = _ids(link, title)
    category, _ = classify(title, summary, source)
    return (
        news_id,
        link[:2048],
        title[:500],
        summary[:2000],
        body or None,
        source,
        published,
        category,
        TRUST,
        chash,
        body_source,
        truncated,
    )


async def _rss_rows(client: Any, source: str, feed_url: str, cap: int) -> list[tuple[Any, ...]]:
    rec = await fetch_feed(client, feed_url)
    if rec["error"]:
        raise ValueError(f"{rec['http_status']} {rec['error']}")
    rows: list[tuple[Any, ...]] = []
    for item in rec["items"][:cap]:
        title = (item.get("title") or "").strip()
        link = (item.get("link") or item.get("guid") or "").strip()
        if not title or not link:
            continue
        pub = parse_ts(item.get("date", "")) or datetime.now(UTC).timestamp()
        ext = clean_item(item, source)
        rows.append(
            _row(
                source,
                title,
                link,
                item.get("description") or "",
                ext["body"],
                datetime.fromtimestamp(pub, tz=UTC),
                ext["body_source"],
                ext["truncated"],
            )
        )
    return rows


async def _sitemap_rows(client: Any, source: str, feed_url: str, cap: int) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for url, stamp in await sitemap_articles(client, feed_url, cap):
        try:
            page = await client.get(url)
        except Exception:
            continue
        if page.status_code != 200:
            continue
        title, summary, prose = article_text(page.text)
        if not title:
            continue
        pub = parse_ts(stamp) or datetime.now(UTC).timestamp()
        rows.append(
            _row(
                source,
                title,
                url,
                summary,
                prose,
                datetime.fromtimestamp(pub, tz=UTC),
                "article_html",
                len(prose) < 800,
            )
        )
    return rows


async def ingest_source(
    conn: Any, client: Any, source: str, feed_url: str, kind: str, cap: int = 20
) -> dict[str, Any]:
    """Fetch one source's newest *cap* items and insert the unseen ones."""
    stats: dict[str, Any] = {
        "source": source,
        "kind": kind,
        "seen": 0,
        "new": 0,
        "duplicate": 0,
        "error": None,
    }
    try:
        rows = (
            await _rss_rows(client, source, feed_url, cap)
            if kind == "rss"
            else await _sitemap_rows(client, source, feed_url, cap)
        )
    except Exception as exc:  # a dead source is data, not a crashed batch
        stats["error"] = str(exc)[:160]
        if kind == "rss":
            await record_status(
                conn, source=source, url=feed_url, status="error", error=stats["error"]
            )
        return stats
    if kind == "rss":
        await record_status(
            conn,
            source=source,
            url=feed_url,
            status="ok",
            http_status=200,
            items=len(rows),
            new_items=0,
        )
    stats["seen"] = len(rows)
    for row in rows:
        exists = await conn.fetchval(
            "SELECT 1 FROM news_items WHERE news_id=$1 OR content_hash=$2 LIMIT 1", row[0], row[9]
        )
        if exists:
            stats["duplicate"] += 1
            continue
        await conn.execute(INSERT, *row)
        stats["new"] += 1
    return stats


__all__ = ["TRUST", "ingest_source"]
