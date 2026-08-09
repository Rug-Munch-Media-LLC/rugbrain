#!/usr/bin/env python3
"""CryptoPanic news RSS → Postgres news_items → (news_to_rag.py embeds → news_v2).

CryptoPanic (https://cryptopanic.com) aggregates crypto news with community
voting ("panic score"). Adding it as a source widens the bot's market-awareness
RAG beyond the existing CoinDesk/CoinTelegraph/Decrypt feeds: CryptoPanic
surfaces the stories the community is *actually talking about*, which is
high-value context for market moves.

Source: public RSS at https://cryptopanic.com/news/rss/ (RSS 2.0, ~20 items).
The API plan (https://cryptopanic.com/api/v1/posts/...) requires an auth token;
when ``CRYPTOPANIC_API_TOKEN`` is set this script uses the authenticated
``format=rss`` endpoint instead of the public URL. Without a token the public
RSS is attempted first — if it returns HTML instead of XML (Cloudflare bot
block, which has affected datacenter IPs since the free API plan was
discontinued 2026-04-01), the script logs a warning and exits 0 so the cron
pipeline stays healthy and starts ingesting automatically once a token exists.

Why Postgres first (NOT direct to Qdrant):
  - ``news_v2`` is the canonical news collection written by ``news_to_rag.py``,
    whose ``--purge-orphans`` mode deletes points whose ``metadata.news_id`` has
    no row in Postgres ``news_items``. Inserting here (source=CryptoPanic) with
    the same stable id scheme makes the points durable across purges.
  - ``news_to_rag.py`` picks up rows where ``rag_embedding_id IS NULL`` on its
    next run (15-min / hourly cron), reusing the rating engine + chunking.

Idempotent: news_id is a deterministic hash of the article URL, and inserts use
``ON CONFLICT DO NOTHING``, so re-running never duplicates articles.

Usage:
    python scripts/ingest_cryptopanic_news.py --days 7
    python scripts/ingest_cryptopanic_news.py --feed-url https://cryptopanic.com/news/rss/
    python scripts/ingest_cryptopanic_news.py --dry-run

Env:
  POSTGRES_HOST/PORT/USER/PASSWORD/DB   (defaults: 127.0.0.1:5432 rmi/rmi)
  CRYPTOPANIC_API_TOKEN                 optional auth token (API RSS)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import html as html_lib
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import asyncpg
import httpx
from defusedxml import ElementTree as element_tree  # noqa: N813

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_cryptopanic")

PUBLIC_RSS_URL = "https://cryptopanic.com/news/rss/"
API_RSS_URL = "https://cryptopanic.com/api/v1/posts/"
SOURCE = "CryptoPanic"
REQUEST_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) RugMunch-RAG/1.0 (admin@rugmunch.io)"


def _stable_news_id(url: str) -> str:
    """Stable news_id from the article URL (matches backend _stable_id shape)."""
    h = hashlib.sha256(f"{SOURCE}:{url}".encode()).hexdigest()[:24]
    return f"cryptopanic:{h}"


def _detect_chains(text: str) -> list[str]:
    """Heuristic chain detection (mirrors rmi-backend ingest.py)."""
    text_l = text.lower()
    chains: list[str] = []
    chain_map = {
        "solana": ["solana", "sol ", "$sol"],
        "ethereum": ["ethereum", "eth ", "$eth", "ether"],
        "bitcoin": ["bitcoin", "btc ", "$btc"],
        "base": ["base ", "basechain", "coinbase base"],
        "arbitrum": ["arbitrum", "arb "],
        "polygon": ["polygon", "matic"],
        "bsc": ["bsc", "bnb chain", "binance smart chain"],
        "tron": ["tron", "trx "],
        "avalanche": ["avalanche", "avax"],
    }
    for chain, keywords in chain_map.items():
        if any(k in text_l for k in keywords):
            chains.append(chain)
    return chains


def _extract_tickers(text: str) -> list[str]:
    return list(set(re.findall(r"\$([A-Z]{2,6})\b", text)))


def _resolve_feed_url() -> str:
    token = os.getenv("CRYPTOPANIC_API_TOKEN", "").strip()
    if token:
        return f"{API_RSS_URL}?auth_token={token}&public=true&kind=news&format=rss"
    return PUBLIC_RSS_URL


def _looks_like_html(body: str) -> bool:
    head = body.lstrip()[:512].lower()
    return "<!doctype html" in head or "<html" in head


def _parse_rss(body: str) -> list[dict[str, Any]]:
    """Parse an RSS 2.0 body leniently (survives malformed XML like SEC feeds)."""
    fixed = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", body)
    items: list[dict[str, Any]] = []
    try:
        root = element_tree.fromstring(fixed)
        for it in root.findall(".//item"):
            items.append(
                {
                    "title": html_lib.unescape(it.findtext("title") or "").strip(),
                    "link": (it.findtext("link") or "").strip(),
                    "guid": (it.findtext("guid") or "").strip(),
                    "description": html_lib.unescape(it.findtext("description") or "").strip(),
                    "pubDate": (it.findtext("pubDate") or "").strip(),
                }
            )
    except element_tree.ParseError:
        logger.warning("rss_parse_fallback_regex")
        for block in re.split(r"<item>", fixed)[1:]:
            item: dict[str, Any] = {
                "title": "",
                "link": "",
                "guid": "",
                "description": "",
                "pubDate": "",
            }
            m = re.search(r"<title>(.*?)</title>", block, re.S)
            if m:
                item["title"] = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            m = re.search(r"<link>(.*?)</link>", block, re.S)
            if m:
                item["link"] = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            m = re.search(r"<guid>(.*?)</guid>", block, re.S)
            if m:
                item["guid"] = html_lib.unescape(m.group(1)).strip()
            m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
            if m:
                item["pubDate"] = html_lib.unescape(m.group(1)).strip()
            if item["title"] or item["link"]:
                items.append(item)
    return items


async def fetch_and_parse(client: httpx.AsyncClient, feed_url: str) -> list[dict[str, Any]]:
    """Fetch the feed and return parsed items. Raises FeedBlocked for HTML."""
    r = await client.get(feed_url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    body = r.text
    if _looks_like_html(body):
        raise RuntimeError(
            "cryptopanic_feed_blocked: server returned HTML instead of RSS "
            "(public feed bot-blocked; set CRYPTOPANIC_API_TOKEN for the API RSS)"
        )
    items = _parse_rss(body)
    logger.info(
        "rss_items total=%d url=%s", len(items), "api" if "auth_token" in feed_url else "public"
    )
    return items


async def run(days: int | None, dry_run: bool, feed_url: str | None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": "news_v2 (via Postgres news_items + news_to_rag.py)",
        "source": SOURCE,
        "feed_items": 0,
        "new": 0,
        "duplicate": 0,
        "errors": 0,
        "blocked": False,
        "dry_run": dry_run,
    }
    url = feed_url or _resolve_feed_url()
    pg_host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    pg_port = int(os.getenv("POSTGRES_PORT", "5432"))
    pg_user = os.getenv("POSTGRES_USER", "rmi")
    pg_pass = os.getenv("POSTGRES_PASSWORD", "")
    pg_db = os.getenv("POSTGRES_DB", "rmi")

    conn: asyncpg.Connection | None = None
    if not dry_run:
        try:
            conn = await asyncpg.connect(
                host=pg_host,
                port=pg_port,
                user=pg_user,
                password=pg_pass,
                database=pg_db,
                timeout=8,
            )
        except (OSError, asyncpg.PostgresError) as e:
            logger.warning("pg_connect_failed err=%s", e)
            stats["errors"] += 1
            return stats

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            try:
                items = await fetch_and_parse(client, url)
            except Exception as e:  # blocked or network
                msg = str(e)
                if "blocked" in msg:
                    stats["blocked"] = True
                    logger.warning("cryptopanic_blocked: %s", msg)
                else:
                    stats["errors"] += 1
                    logger.warning("cryptopanic_fetch_failed err=%s", msg[:200])
                return stats
            stats["feed_items"] = len(items)

            cutoff: datetime | None = None
            if days:
                cutoff = datetime.now(UTC) - timedelta(days=days)

            for it in items:
                title = it["title"]
                link = it["link"] or it["guid"]
                if not title or not link:
                    stats["errors"] += 1
                    continue
                pub_ts = 0.0
                if it["pubDate"]:
                    with contextlib.suppress(ValueError, TypeError):
                        pub_ts = parsedate_to_datetime(it["pubDate"]).timestamp()
                if cutoff is not None and pub_ts and pub_ts < cutoff.timestamp():
                    continue
                pub_dt = datetime.fromtimestamp(pub_ts, tz=UTC) if pub_ts else datetime.now(UTC)
                text = f"{title} {it['description']}"
                chains = _detect_chains(text)
                tickers = _extract_tickers(text)
                news_id = _stable_news_id(link)

                if dry_run:
                    stats["new"] += 1
                    continue

                assert conn is not None
                try:
                    existing = await conn.fetchval(
                        "SELECT 1 FROM news_items WHERE news_id=$1", news_id
                    )
                    if existing:
                        stats["duplicate"] += 1
                        continue
                    await conn.execute(
                        """
                        INSERT INTO news_items (
                            news_id, url, title, summary, body_markdown,
                            source, published_at, ingested_at,
                            chains_mentioned, tokens_mentioned, sentiment_score
                        ) VALUES ($1,$2,$3,$4,NULL,$5,$6,now(),$7,$8,NULL)
                        """,
                        news_id,
                        link[:2048],
                        title[:500],
                        (it["description"] or "")[:2000],
                        SOURCE,
                        pub_dt,
                        chains,
                        tickers,
                    )
                    stats["new"] += 1
                except (asyncpg.PostgresError, ValueError, TypeError) as e:
                    stats["errors"] += 1
                    logger.warning("item_insert_fail news_id=%s err=%s", news_id, str(e)[:150])
            if conn is not None:
                await conn.execute("COMMIT")
    finally:
        if conn is not None:
            await conn.close()

    logger.info(
        "cryptopanic_done feed=%d new=%d dup=%d errors=%d blocked=%s dry_run=%s",
        stats["feed_items"],
        stats["new"],
        stats["duplicate"],
        stats["errors"],
        stats["blocked"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CryptoPanic RSS → Postgres news_items (news_to_rag.py embeds to news_v2)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="only items within the last N days (incremental cron mode)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch + parse but do not write to Postgres"
    )
    parser.add_argument(
        "--feed-url", default=None, help="override feed URL (public RSS or API RSS with token)"
    )
    args = parser.parse_args()

    stats = asyncio.run(run(args.days, args.dry_run, args.feed_url))
    import json

    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
