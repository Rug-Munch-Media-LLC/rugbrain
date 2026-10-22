"""Row building + insert for the RSS ingest (news_items).

Kept apart from the orchestrator so the script stays a thin runner and the
row shape has exactly one definition. Every function here is pure except
``store_feed``, which needs an asyncpg connection.

Idempotency: rows carry both ``news_id`` (legacy URL hash, matches the id the
previous single-feed ingest wrote, so ``news_to_rag.py`` keeps picking them up)
and ``content_hash`` (normalised title + guid|link). The URL check catches the
same link re-crawled; the content check catches the same story reposted by an
aggregator under its own URL.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import asyncpg

from rugbrain.rss_classify import classify
from rugbrain.rss_extract import clean_item
from rugbrain.rss_feed import fetch_feed, newest_epoch
from rugbrain.rss_feed_status import conditional_headers, record_status, remember_validators

PREFIX = "cryptopanic"  # legacy id namespace — do not rename, RAG joins on it
INSERT = """
INSERT INTO news_items (news_id, url, title, summary, body_markdown, source,
                        published_at, ingested_at, chains_mentioned, tokens_mentioned,
                        sentiment_score, category, source_trust, content_hash,
                        body_source, truncated)
VALUES ($1,$2,$3,$4,$5,$6,$7,now(),$8,$9,NULL,$10,$11,$12,$13,$14)
ON CONFLICT DO NOTHING  -- news_id PK or content_hash unique index
"""
CHAINS = {
    "solana": ["solana", " sol ", "$sol"],
    "ethereum": ["ethereum", " eth ", "$eth", "ether"],
    "bitcoin": ["bitcoin", " btc ", "$btc"],
    "base": ["base ", "basechain"],
    "arbitrum": ["arbitrum", " arb "],
    "polygon": ["polygon", "matic"],
    "bsc": ["bsc", "bnb chain"],
    "tron": ["tron", "trx "],
    "avalanche": ["avalanche", "avax"],
}


def content_hash(title: str, guid: str, link: str) -> str:
    """Stable per-story hash: normalised title + identity, so reposts collapse."""
    title_norm = re.sub(r"[^a-z0-9 ]", "", (title or "").lower())
    title_norm = re.sub(r"\s+", " ", title_norm).strip()
    return hashlib.sha256(f"{title_norm}|{guid or link}".encode()).hexdigest()[:32]


def news_id_for(link: str) -> str:
    """Legacy-compatible stable id: ``cryptopanic:<sha256[:24]>``."""
    return f"{PREFIX}:{hashlib.sha256(f'{PREFIX}:{link}'.encode()).hexdigest()[:24]}"


def _row(source: str, item: dict[str, Any], pub: float) -> tuple[Any, ...]:
    title = (item.get("title") or "").strip()
    link = (item.get("link") or item.get("guid") or "").strip()
    desc = item.get("description") or ""
    text = f"{title} {desc}"
    low = f" {text.lower()} "
    chains = [c for c, keys in CHAINS.items() if any(k in low for k in keys)]
    tickers = sorted(set(re.findall(r"\$([A-Z]{2,6})\b", text)))
    category, trust = classify(title, desc, source)
    extracted = clean_item(item, source)
    return (
        news_id_for(link),
        link[:2048],
        title[:500],
        desc[:2000],
        extracted["body"][:20000],
        source,
        datetime.fromtimestamp(pub, tz=UTC),
        chains,
        tickers,
        category,
        trust,
        content_hash(title, item.get("guid", ""), link),
        extracted["body_source"],
        extracted["truncated"],
    )


async def store_feed(
    conn: asyncpg.Connection,
    validators: dict[str, dict[str, str]],
    url: str,
    source: str,
    cutoff: datetime | None,
    client: Any,
) -> dict[str, Any]:
    """Fetch one feed and insert its unseen rows. Never raises; returns stats."""
    stats: dict[str, Any] = {"items": 0, "new": 0, "dup": 0, "error": None, "not_modified": False}
    rec = await fetch_feed(client, url, conditional_headers(validators, url))
    if rec["not_modified"]:
        stats["not_modified"] = True
        await record_status(
            conn, source=source, url=url, status="not_modified", http_status=304, not_modified=True
        )
        return stats
    if rec["error"]:
        stats["error"] = rec["error"]
        await record_status(
            conn,
            source=source,
            url=url,
            status="error",
            http_status=rec["http_status"],
            error=rec["error"],
        )
        return stats

    remember_validators(validators, url, rec["etag"], rec["last_modified"])
    for item in rec["items"]:
        title = (item.get("title") or "").strip()
        link = (item.get("link") or item.get("guid") or "").strip()
        if not title or not link:
            continue
        stats["items"] += 1
        pub = newest_epoch([item]) or datetime.now(UTC).timestamp()
        if cutoff and pub < cutoff.timestamp():
            continue
        row = _row(source, item, pub)
        try:
            if await conn.fetchval(
                "SELECT 1 FROM news_items WHERE content_hash=$1 LIMIT 1", row[-1]
            ):
                stats["dup"] += 1
                continue
            await conn.execute(INSERT, *row)
            stats["new"] += 1
        except (asyncpg.PostgresError, ValueError, TypeError) as exc:
            stats["error"] = f"insert: {str(exc)[:160]}"
    await record_status(
        conn,
        source=source,
        url=url,
        status="ok",
        http_status=200,
        items=stats["items"],
        new_items=stats["new"],
        etag=rec["etag"],
        last_modified=rec["last_modified"],
    )
    return stats
