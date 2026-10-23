#!/usr/bin/env python3
"""Recover articles from dead-RSS sources into Postgres ``news_items``.

Sources flagged DEAD in the databus status file whose SITE still publishes
(e.g. the Medium-era 410 feeds, Cloudflare-403 feeds) are recovered cheapest
first:

  1. the site's sitemap — plain XML, no JS; an index is followed to its
     article sitemaps, and only recent, article-shaped URLs are kept;
  2. a listing scrape through Pry (jsRender + Cloudflare bypass) when the
     sitemap is absent or blocked.

Blocked hosts are fetched through FlareSolverr (127.0.0.1:8192, the pry
stack's own bypass) before giving up. Articles land in ``news_items`` — the
same intake RSS uses — so ``news_to_rag.py`` embeds them into ``news_v2_8b``
on its next run (its query picks up every row with rag_embedding_id NULL).

Usage:
    python3 scripts/scrape_dead_sources.py --dry-run --limit 5
    python3 scripts/scrape_dead_sources.py --max-per-source 10
    python3 scripts/scrape_dead_sources.py --source kraken-com

Env: POSTGRES_* (or RMI_SECRETS_FILE sourced first), PRYSCRAPER_URL,
     FLARESOLVERR_URL (default http://127.0.0.1:8192), PRY_INTERNAL_TOKEN,
     PRY_INTERNAL_CALLER, PRY_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import logging
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scrape_dead_sources")

SOURCE = "dead_feed_scraper"
STATUS_FILE = Path(
    os.getenv(
        "NEWS_STATUS_FILE",
        "/srv/work/repos/rmi-backend/app/databus/news_sources_status.json",
    )
)
_SRC_ROOT = str(Path(__file__).resolve().parents[1] / "src")
_NEWS_SOURCES_FILE = Path("/srv/work/repos/rmi-backend/app/databus/unified_news_sources.json")
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
SITEMAP_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/blog/sitemap.xml",
    "/news/sitemap.xml",
    "/en/sitemap.xml",
)
#: URL shapes that look like an article (not a category/tag/author page).
ARTICLE_RE = re.compile(
    r"(/blog/|/news/|/posts?/|/articles?/|/insights?/|/research/|/\d{4}/)",
    re.I,
)
SKIP_RE = re.compile(
    r"(/tag/|/category/|/author/|/page/\d|/topics?/|/about|/contact|/privacy|"
    r"/terms|/careers?|/pricing|/login|/signup|/cookie)",
    re.I,
)


def _stable_news_id(url: str) -> str:
    """Stable news_id from the article URL (same shape as the RSS intake)."""
    h = hashlib.sha256(f"{SOURCE}:{url}".encode()).hexdigest()[:24]
    return f"scraped:{h}"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


async def _flare_get(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch through FlareSolverr (the pry stack's Cloudflare bypass)."""
    base = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8192")
    try:
        r = await client.post(
            f"{base}/v1",
            json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
            timeout=90.0,
        )
        sol = (r.json() or {}).get("solution") or {}
        return sol.get("response")
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("flare failed %s: %s", url, exc)
        return None


async def _pry_scrape(client: httpx.AsyncClient, url: str) -> str | None:
    """Listing/JS fetch through Pry with the internal credential."""
    sys.path.insert(0, _SRC_ROOT)
    from rugbrain.ingest_sources.x_intel import pry_internal_auth

    base = os.getenv("PRYSCRAPER_URL", "http://127.0.0.1:8005").rstrip("/")
    headers = {"Content-Type": "application/json"}
    key = os.getenv("PRY_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(pry_internal_auth.pry_internal_headers())
    try:
        r = await client.post(
            f"{base}{pry_internal_auth.PRY_SCRAPE_PATH}",
            headers=headers,
            json={"url": url, "formats": ["markdown"], "bypassCloudflare": True, "jsRender": True},
            timeout=90.0,
        )
        if r.status_code != 200:
            return None
        return ((r.json() or {}).get("data") or {}).get("markdown")
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("pry failed %s: %s", url, exc)
        return None


async def _fetch(client: httpx.AsyncClient, url: str) -> str | None:
    """Direct GET, then FlareSolverr on any challenge/block."""
    try:
        r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True)
        if r.status_code == 200 and r.content:
            return r.text
    except httpx.HTTPError:
        pass
    return await _flare_get(client, url)


async def _fetch_plain(client: httpx.AsyncClient, url: str) -> str | None:
    """Direct GET only. NEVER falls back to FlareSolverr.

    Sitemaps are machine-readable XML. If one is behind a challenge it is not
    worth a 20 s bypass, and probing SITEMAP_PATHS x every origin through the
    bypass was ~90% of all scrape-tier traffic at 0% success.
    """
    try:
        r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True)
        if r.status_code == 200 and r.content:
            return r.text
    except httpx.HTTPError:
        pass
    return None


def _locs(xml: str) -> list[str]:
    return [html.unescape(m) for m in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)]


async def _sitemap_articles(client: httpx.AsyncClient, origin: str, cap: int) -> list[str]:
    """Recent article URLs from the site's sitemap (index followed one level)."""
    found: list[str] = []
    # robots.txt is the sanctioned way to find the sitemap; try it before
    # probing a fixed path list.
    _robots = await _fetch_plain(client, origin + "/robots.txt")
    _paths = list(SITEMAP_PATHS)
    if _robots:
        _paths = [m for m in re.findall(r"(?i)^sitemap:\s*(\S+)", _robots, re.M)][:2] + _paths
    for path in _paths:
        if path.startswith("http"):
            body = await _fetch_plain(client, path)
        else:
            body = await _fetch_plain(client, origin + path)
        if not body:
            continue
        locs = _locs(body)
        # sitemap index -> follow article-ish sub-sitemaps (bounded)
        subs = [u for u in locs if u.endswith(".xml")][:4]
        if subs:
            for sub in subs:
                sub_body = await _fetch_plain(client, sub)
                if sub_body:
                    locs.extend(_locs(sub_body))
        for u in locs:
            if u.endswith(".xml") or SKIP_RE.search(u):
                continue
            if ARTICLE_RE.search(u):
                found.append(u)
        if found:
            break
    # newest-ish first by numeric id in the URL, dedupe, cap
    seen: set[str] = set()
    out: list[str] = []
    for u in reversed(found):
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= cap:
            break
    return out


async def _pry_listing_articles(client: httpx.AsyncClient, origin: str, cap: int) -> list[str]:
    """Fallback: scrape the blog listing via Pry and pull article links."""
    for path in ("/blog", "/news", ""):
        md = await _pry_scrape(client, origin + path)
        if not md:
            continue
        links = re.findall(r"\]\((https?://[^)\s]+)\)", md)
        arts = [
            u
            for u in links
            if urlsplit(u).netloc == urlsplit(origin).netloc
            and not SKIP_RE.search(u)
            and ARTICLE_RE.search(u)
        ]
        if arts:
            return list(dict.fromkeys(arts))[:cap]
    return []


def _extract_article(body: str) -> tuple[str, str, str]:
    """(title, summary, body_text) from an article HTML page."""
    title = ""
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', body, re.I)
    if m:
        title = html.unescape(m.group(1))
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
        title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    summary = ""
    m = re.search(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)',
        body,
        re.I,
    )
    if m:
        summary = html.unescape(m.group(1))[:2000]
    text = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return title[:500], summary, text[:24000]


async def _ingest_source(
    client: httpx.AsyncClient,
    conn: asyncpg.Connection | None,
    source_id: str,
    feed_url: str,
    cap: int,
    dry_run: bool,
) -> dict[str, int]:
    stats = {"found": 0, "new": 0, "duplicate": 0, "errors": 0}
    origin = _origin(feed_url)
    urls = await _sitemap_articles(client, origin, cap)
    if not urls:
        urls = await _pry_listing_articles(client, origin, cap)
    stats["found"] = len(urls)
    for url in urls:
        try:
            body = await _fetch(client, url)
            if not body:
                stats["errors"] += 1
                continue
            title, summary, text = _extract_article(body)
            if not title:
                stats["errors"] += 1
                continue
            if dry_run:
                stats["new"] += 1
                continue
            news_id = _stable_news_id(url)
            assert conn is not None
            existing = await conn.fetchval("SELECT 1 FROM news_items WHERE news_id=$1", news_id)
            if existing:
                stats["duplicate"] += 1
                continue
            await conn.execute(
                """
                INSERT INTO news_items (
                    news_id, url, title, summary, body_markdown,
                    source, published_at, ingested_at,
                    chains_mentioned, tokens_mentioned, sentiment_score
                ) VALUES ($1,$2,$3,$4,$5,$6,now(),now(),ARRAY[]::TEXT[],ARRAY[]::TEXT[],NULL)
                """,
                news_id,
                url[:2048],
                title,
                summary or text[:2000],
                text,
                source_id,
            )
            stats["new"] += 1
        except (asyncpg.PostgresError, ValueError, TypeError) as exc:
            log.warning("ingest failed %s: %s", url, exc)
            stats["errors"] += 1
    return stats


async def _main(args: argparse.Namespace) -> int:
    status = json.loads(await asyncio.to_thread(STATUS_FILE.read_text))["sources"]
    data = json.loads(await asyncio.to_thread(_NEWS_SOURCES_FILE.read_text))
    by_id = {s["id"]: s for s in data["sources"]}
    dead = [
        sid for sid, st in status.items() if st.get("status") in ("DEAD", "STALE") and sid in by_id
    ]
    if args.source:
        dead = [s for s in dead if args.source in s]
    if args.limit:
        dead = dead[: args.limit]
    log.info("recovering %d dead sources (dry_run=%s)", len(dead), args.dry_run)

    conn = None
    if not args.dry_run:
        conn = await asyncpg.connect(
            host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            user=os.getenv("POSTGRES_USER", "rmi"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
            database=os.getenv("POSTGRES_DB", "rmi"),
        )
    total = {"found": 0, "new": 0, "duplicate": 0, "errors": 0}
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            for sid in dead:
                stats = await _ingest_source(
                    client, conn, sid, by_id[sid]["url"], args.max_per_source, args.dry_run
                )
                for k in total:
                    total[k] += stats[k]
                log.info("%s: %s", sid, stats)
                await asyncio.sleep(1.0)  # be polite to the sites
    finally:
        if conn is not None:
            await conn.close()
    log.info("TOTAL: %s", total)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="max sources this run")
    ap.add_argument("--source", default="", help="substring filter on source id")
    ap.add_argument("--max-per-source", type=int, default=10)
    args = ap.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
