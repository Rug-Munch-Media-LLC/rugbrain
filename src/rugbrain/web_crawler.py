"""crawl4ai-powered web crawler for RugBrain ingestion.

Uses `crawl4ai <https://github.com/unclecode/crawl4ai>`_ for intelligent,
JS-rendered crawling with built-in markdown extraction. crawl4ai is an
*optional* dependency (install with ``pip install "rugbrain[crawl]"``); when
it is absent (or Playwright's Chromium is not installed) the crawler
degrades gracefully to plain HTTP fetching + lightweight HTML-to-text
extraction, so the pipeline keeps working in minimal environments.

Pipeline for every source: fetch → extract clean text → chunk → embed
(Qwen3-Embedding-8B via ``Qwen3Embedder``) → upsert into Qdrant
with deterministic IDs (idempotent).

Politeness features:

  * per-domain rate limiting (``CRAWLER_MIN_INTERVAL`` seconds between hits)
  * robots.txt respect — crawl4ai's native ``check_robots_txt`` plus a local
    cache used by the HTTP fallback path
  * a descriptive ``User-Agent`` on every request

Public crawl entry points:

  * ``crawl_crypto_news()``      — RSS + homepage crawl of crypto news sites
  * ``crawl_security_feeds()``   — security blogs (CertiK, PeckShield, ...)
  * ``crawl_social_sentiment()`` — crypto X/Twitter via SearXNG (local, port 8089)
  * ``crawl_github_audits()``    — GitHub audit reports, post-mortems, PoCs

Usage::

    python -m rugbrain.web_crawler --news --security --limit 10
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from rugbrain.ingest_common import CrawlDocument, doc_from_article
from rugbrain.rss_parser import parse_feed
from rugbrain.web_crawler_pkg.config import (
    CRAWL4AI_TIMEOUT as CRAWL4AI_TIMEOUT,
)
from rugbrain.web_crawler_pkg.config import (
    CRAWLER_MIN_INTERVAL as CRAWLER_MIN_INTERVAL,
)
from rugbrain.web_crawler_pkg.config import (
    CRAWLER_USER_AGENT as CRAWLER_USER_AGENT,
)
from rugbrain.web_crawler_pkg.config import (
    GITHUB_COLLECTION as GITHUB_COLLECTION,
)
from rugbrain.web_crawler_pkg.config import (
    GITHUB_QUERIES as GITHUB_QUERIES,
)
from rugbrain.web_crawler_pkg.config import (
    HTTP_TIMEOUT as HTTP_TIMEOUT,
)
from rugbrain.web_crawler_pkg.config import (
    NEWS_COLLECTION as NEWS_COLLECTION,
)
from rugbrain.web_crawler_pkg.config import (
    NEWS_FEEDS as NEWS_FEEDS,
)
from rugbrain.web_crawler_pkg.config import (
    ROBOTS_TTL as ROBOTS_TTL,
)
from rugbrain.web_crawler_pkg.config import (
    SEARXNG_URL as SEARXNG_URL,
)
from rugbrain.web_crawler_pkg.config import (
    SECURITY_BLOGS as SECURITY_BLOGS,
)
from rugbrain.web_crawler_pkg.config import (
    SECURITY_COLLECTION as SECURITY_COLLECTION,
)
from rugbrain.web_crawler_pkg.config import (
    SOCIAL_COLLECTION as SOCIAL_COLLECTION,
)
from rugbrain.web_crawler_pkg.config import (
    X_ACCOUNTS as X_ACCOUNTS,
)
from rugbrain.web_crawler_pkg.config import (
    CrawlerSource as CrawlerSource,
)
from rugbrain.web_crawler_pkg.crawler import (
    WebCrawler as WebCrawler,
)
from rugbrain.web_crawler_pkg.html_text import (
    html_to_text as html_to_text,
)
from rugbrain.web_crawler_pkg.politeness import (
    DomainRateLimiter as DomainRateLimiter,
)
from rugbrain.web_crawler_pkg.politeness import (
    RobotsTxtCache as RobotsTxtCache,
)

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────
# ── Feed-based source fetchers ───────────────────────────────────────────


async def fetch_rss_feed(
    url: str, source: str, collection: str, category: str, limit: int = 15
) -> list[CrawlDocument]:
    """Fetch an RSS/Atom feed and convert items into documents."""
    docs: list[CrawlDocument] = []
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": CRAWLER_USER_AGENT}
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                log.debug("feed_fetch_status url=%s status=%d", url, r.status_code)
                return docs
            feed_title, items = parse_feed(r.text, limit=limit)
    except Exception as e:
        log.warning("feed_fetch_failed url=%s err=%s", url, str(e)[:120])
        return docs
    for item in items:
        if not item["title"] and not item["summary"]:
            continue
        docs.append(
            doc_from_article(
                title=item["title"],
                content=f"{item['title']}. {item['summary']}".strip(),
                url=item["url"] or url,
                source=source,
                collection=collection,
                category=category,
                published=item["published"],
                metadata={"feed_title": feed_title},
            )
        )
    return docs


# ── Public crawl entry points ────────────────────────────────────────────


async def crawl_crypto_news(
    crawler: WebCrawler | None = None, limit: int = 30, collection: str = NEWS_COLLECTION
) -> dict[str, int]:
    """Crawl configured news sources for new articles (RSS-first)."""
    c = crawler or WebCrawler()
    docs: list[CrawlDocument] = []
    for source, feed_url in NEWS_FEEDS:
        feed_docs = await fetch_rss_feed(feed_url, source, collection, "crypto_news", limit=8)
        if feed_docs:
            docs.extend(feed_docs)
            continue
        # RSS failed → crawl the homepage via crawl4ai (or HTTP fallback).
        page = await c.crawl(
            urlparse(feed_url).scheme + "://" + urlparse(feed_url).netloc,
            source=source,
            category="crypto_news",
            collection=collection,
        )
        if page.content:
            docs.append(page)
    log.info("crawl_crypto_news sources=%d docs=%d", len(NEWS_FEEDS), len(docs))
    return await c.store(docs[:limit], collection)


async def crawl_security_feeds(
    crawler: WebCrawler | None = None, limit: int = 20, collection: str = SECURITY_COLLECTION
) -> dict[str, int]:
    """Crawl security blogs (CertiK, PeckShield, SlowMist, Rekt, BlockSec)."""
    c = crawler or WebCrawler()
    urls = [u for s in SECURITY_BLOGS for u in s.start_urls]
    docs = await c.crawl_many(
        urls,
        source="security_blogs",
        category="security_blog",
        collection=collection,
        limit=limit,
    )
    log.info("crawl_security_feeds blogs=%d docs=%d", len(SECURITY_BLOGS), len(docs))
    return await c.store(docs, collection)


async def crawl_social_sentiment(
    crawler: WebCrawler | None = None, limit: int = 30, collection: str = SOCIAL_COLLECTION
) -> dict[str, int]:
    """Crawl crypto X/Twitter via SearXNG (local, port 8089) for each tracked account."""
    c = crawler or WebCrawler()
    docs: list[CrawlDocument] = []
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, headers={"User-Agent": CRAWLER_USER_AGENT}
    ) as client:
        for handle in X_ACCOUNTS:
            try:
                r = await client.get(
                    f"{SEARXNG_URL}/search",
                    params={
                        "q": f"site:x.com/{handle} OR site:nitter.net/{handle} crypto",
                        "format": "json",
                        "safesearch": "0",
                        "time_range": "day",
                    },
                )
                if r.status_code != 200:
                    continue
                results = (r.json() or {}).get("results", []) or []
                for res in results[:5]:
                    if not isinstance(res, dict):
                        continue
                    title = str(res.get("title", "")).strip()
                    if not title:
                        continue
                    docs.append(
                        doc_from_article(
                            title=title[:300],
                            content=f"{title}. {res.get('content', '')}".strip()[:2000],
                            url=str(res.get("url", "")),
                            source=f"x:{handle}",
                            collection=collection,
                            category="crypto_twitter",
                            metadata={"handle": handle, "engine": "searxng"},
                        )
                    )
            except Exception as e:
                log.debug("searxng_account_failed handle=%s err=%s", handle, str(e)[:120])
    log.info("crawl_social_sentiment accounts=%d docs=%d", len(X_ACCOUNTS), len(docs))
    return await c.store(docs[:limit], collection)


async def crawl_github_audits(
    crawler: WebCrawler | None = None, limit: int = 15, collection: str = GITHUB_COLLECTION
) -> dict[str, int]:
    """Crawl GitHub for new audit reports, post-mortems, and PoCs.

    Uses the free GitHub search API (60 req/hr unauthenticated) to find
    recently-updated repos matching security-audit queries, then fetches each
    repo's README for clean text.
    """
    c = crawler or WebCrawler()
    docs: list[CrawlDocument] = []
    headers = {"Accept": "application/vnd.github.raw+json", "User-Agent": CRAWLER_USER_AGENT}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
        seen_repos: set[str] = set()
        for query in GITHUB_QUERIES:
            try:
                r = await client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": query, "sort": "updated", "order": "desc", "per_page": 5},
                )
                if r.status_code != 200:
                    log.debug("github_search_status query=%r status=%d", query, r.status_code)
                    continue
                for repo in (r.json().get("items") or [])[:5]:
                    full_name = str(repo.get("full_name", ""))
                    if not full_name or full_name in seen_repos:
                        continue
                    seen_repos.add(full_name)
                    readme_url = f"https://api.github.com/repos/{full_name}/readme"
                    rr = await client.get(readme_url)
                    if rr.status_code != 200:
                        continue
                    readme = (rr.text or "").strip()[:20000]
                    if not readme:
                        continue
                    docs.append(
                        doc_from_article(
                            title=str(repo.get("description") or full_name)[:300],
                            content=readme,
                            url=str(repo.get("html_url") or readme_url),
                            source="github_audits",
                            collection=collection,
                            category="audit_report",
                            published=str(repo.get("updated_at", ""))[:10],
                            metadata={
                                "repo": full_name,
                                "stars": int(repo.get("stargazers_count") or 0),
                                "query": query,
                            },
                        )
                    )
            except Exception as e:
                log.debug("github_query_failed query=%r err=%s", query, str(e)[:120])
    log.info("crawl_github_audits queries=%d docs=%d", len(GITHUB_QUERIES), len(docs))
    return await c.store(docs[:limit], collection)


# ── CLI ──────────────────────────────────────────────────────────────────


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="crawl4ai RugBrain web crawler")
    parser.add_argument("--news", action="store_true", help="crawl crypto news feeds")
    parser.add_argument("--security", action="store_true", help="crawl security blogs")
    parser.add_argument("--social", action="store_true", help="crawl X/Twitter via SearXNG")
    parser.add_argument("--github", action="store_true", help="crawl GitHub audits")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true", help="fetch only, don't store")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    async def run() -> None:
        c = WebCrawler()
        if args.dry_run:
            if args.news:
                print(await crawl_crypto_news(c, limit=args.limit))
            if args.security:
                print(await crawl_security_feeds(c, limit=args.limit))
            if args.social:
                print(await crawl_social_sentiment(c, limit=args.limit))
            if args.github:
                print(await crawl_github_audits(c, limit=args.limit))
            if not (args.news or args.security or args.social or args.github):
                parser.print_help()
            return
        totals: dict[str, int] = {}
        if args.news:
            r = await crawl_crypto_news(c, limit=args.limit)
            totals["news"] = r.get("inserted", 0)
        if args.security:
            r = await crawl_security_feeds(c, limit=args.limit)
            totals["security"] = r.get("inserted", 0)
        if args.social:
            r = await crawl_social_sentiment(c, limit=args.limit)
            totals["social"] = r.get("inserted", 0)
        if args.github:
            r = await crawl_github_audits(c, limit=args.limit)
            totals["github"] = r.get("inserted", 0)
        if not totals:
            parser.print_help()
            return
        print(f"WEB CRAWL COMPLETE: {totals}")

    asyncio.run(run())


if __name__ == "__main__":
    _main()


__all__ = [
    "CrawlerSource",
    "DomainRateLimiter",
    "RobotsTxtCache",
    "WebCrawler",
    "crawl_crypto_news",
    "crawl_github_audits",
    "crawl_security_feeds",
    "crawl_social_sentiment",
    "fetch_rss_feed",
    "html_to_text",
]
