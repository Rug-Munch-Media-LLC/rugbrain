"""crawl4ai-powered web crawler for RugBrain ingestion.

Uses `crawl4ai <https://github.com/unclecode/crawl4ai>`_ for intelligent,
JS-rendered crawling with built-in markdown extraction. crawl4ai is an
*optional* dependency (install with ``pip install "rugbrain[crawl]"``); when
it is absent (or Playwright's Chromium is not installed) the crawler
degrades gracefully to plain HTTP fetching + lightweight HTML-to-text
extraction, so the pipeline keeps working in minimal environments.

Pipeline for every source: fetch → extract clean text → chunk → embed
(Infinity BAAI/bge-m3 via ``InfinityBGEEmbedder``) → upsert into Qdrant
with deterministic IDs (idempotent).

Politeness features:

  * per-domain rate limiting (``CRAWLER_MIN_INTERVAL`` seconds between hits)
  * robots.txt respect — crawl4ai's native ``check_robots_txt`` plus a local
    cache used by the HTTP fallback path
  * a descriptive ``User-Agent`` on every request

Public crawl entry points:

  * ``crawl_crypto_news()``      — RSS + homepage crawl of crypto news sites
  * ``crawl_security_feeds()``   — security blogs (CertiK, PeckShield, ...)
  * ``crawl_social_sentiment()`` — crypto X/Twitter via SearXNG (Talos:8089)
  * ``crawl_github_audits()``    — GitHub audit reports, post-mortems, PoCs

Usage::

    python -m rugbrain.web_crawler --news --security --limit 10
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from rugbrain.bge_embedders import InfinityBGEEmbedder
from rugbrain.ingest_common import CrawlDocument, doc_from_article, store_documents
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.rss_parser import parse_feed

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8089").rstrip("/")
CRAWL4AI_TIMEOUT = int(os.getenv("CRAWL4AI_TIMEOUT", "60"))
HTTP_TIMEOUT = int(os.getenv("CRAWLER_HTTP_TIMEOUT", "30"))
CRAWLER_USER_AGENT = os.getenv(
    "CRAWLER_USER_AGENT", "RMI-RAG/1.0 (+https://rugmunch.io; data ingestion)"
)
CRAWLER_MIN_INTERVAL = float(os.getenv("CRAWLER_MIN_INTERVAL", "5"))
ROBOTS_TTL = int(os.getenv("CRAWLER_ROBOTS_TTL", "86400"))

NEWS_COLLECTION = os.getenv("CRAWLER_NEWS_COLLECTION", "news_articles")
SECURITY_COLLECTION = os.getenv("CRAWLER_SECURITY_COLLECTION", "security_feeds")
SOCIAL_COLLECTION = os.getenv("CRAWLER_SOCIAL_COLLECTION", "social_intel")
GITHUB_COLLECTION = os.getenv("CRAWLER_GITHUB_COLLECTION", "github_audits")


@dataclass(frozen=True)
class CrawlerSource:
    """A crawl target: name, Qdrant collection, category, and start URLs."""

    name: str
    collection: str
    category: str
    start_urls: tuple[str, ...]
    rate_limit: float = CRAWLER_MIN_INTERVAL


# Crypto X/Twitter accounts tracked for sentiment (via SearXNG, no X API).
X_ACCOUNTS = tuple(
    os.getenv(
        "CRAWLER_X_ACCOUNTS",
        "VitalikButerin,aeyakovenko,CertikAlert,PeckshieldAlert,SlowMist_Team,zhusu",
    ).split(",")
)

# News sites: RSS feeds first, homepage crawl as fallback.
NEWS_FEEDS: tuple[tuple[str, str], ...] = (
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("theblock", "https://www.theblock.co/rss.xml"),
    ("decrypt", "https://decrypt.co/feed"),
    ("dlnews", "https://www.dlnews.com/rss"),
    ("blockworks", "https://blockworks.co/feed"),
    ("the_defiant", "https://thedefiant.io/feed.xml"),
    ("bitcoinmagazine", "https://bitcoinmagazine.com/feed"),
)

# Security blogs crawled directly (no reliable RSS).
SECURITY_BLOGS: tuple[CrawlerSource, ...] = (
    CrawlerSource("certik", SECURITY_COLLECTION, "security_blog", ("https://www.certik.com/blog",)),
    CrawlerSource("peckshield", SECURITY_COLLECTION, "security_blog", ("https://peckshield.com/",)),
    CrawlerSource(
        "slowmist", SECURITY_COLLECTION, "security_blog", ("https://hacked.slowmist.io/",)
    ),
    CrawlerSource("rekt", SECURITY_COLLECTION, "security_blog", ("https://rekt.news/",)),
    CrawlerSource("blocksec", SECURITY_COLLECTION, "security_blog", ("https://blocksec.com/blog",)),
)

# GitHub search queries for new audit reports / post-mortems / PoCs.
GITHUB_QUERIES: tuple[str, ...] = (
    "smart contract audit report",
    "defi post-mortem exploit",
    "solidity poc vulnerability",
)


# ── Politeness helpers ───────────────────────────────────────────────────


class DomainRateLimiter:
    """Per-domain minimum-interval throttle shared across crawl workers."""

    def __init__(self, default_interval: float = CRAWLER_MIN_INTERVAL) -> None:
        self._interval = default_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    @staticmethod
    def _domain(url: str) -> str:
        return (urlparse(url).netloc or url).lower()

    def _lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def acquire(self, url: str) -> None:
        """Sleep so consecutive requests to *url*'s domain are spaced out."""
        domain = self._domain(url)
        async with self._lock(domain):
            wait = self._interval - (time.monotonic() - self._last.get(domain, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[domain] = time.monotonic()


class RobotsTxtCache:
    """Best-effort robots.txt cache for the HTTP fallback path."""

    def __init__(self, ttl: int = ROBOTS_TTL) -> None:
        self._ttl = ttl
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._user_agent = CRAWLER_USER_AGENT.split("/")[0]

    def _disallow(self, domain: str) -> list[str]:
        entry = self._cache.get(domain)
        if entry and time.monotonic() - entry[0] < self._ttl:
            return entry[1]
        return []

    async def allowed(self, url: str, client: httpx.AsyncClient) -> bool:
        """Return True when robots.txt for *url*'s domain allows crawling."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            return False
        if domain not in self._cache:
            rules: list[str] = []
            try:
                r = await client.get(
                    f"{parsed.scheme}://{domain}/robots.txt", timeout=10.0, follow_redirects=True
                )
                if r.status_code == 200:
                    rules = self._parse_disallow(r.text)
            except Exception:
                rules = []
            self._cache[domain] = (time.monotonic(), rules)
        for disallow in self._disallow(domain):
            if disallow and parsed.path.startswith(disallow):
                return False
        return True

    @staticmethod
    def _parse_disallow(text: str) -> list[str]:
        """Collect ``Disallow`` paths from the default (``*``) user-agent group."""
        paths: list[str] = []
        in_default_group = False
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                in_default_group = value in ("*", CRAWLER_USER_AGENT.split("/")[0])
            elif key == "disallow" and in_default_group and value:
                paths.append(value)
        return paths


# ── HTML → text fallback extraction ──────────────────────────────────────

_SKIP_TAGS_RE = re.compile(
    r"<(script|style|nav|footer|header|aside|form|iframe|svg)[^>]*>.*?</\1>", re.S | re.I
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(raw: str, max_chars: int = 20000) -> str:
    """Lightweight HTML-to-text used when crawl4ai is unavailable."""
    text = _SKIP_TAGS_RE.sub(" ", raw or "")
    text = _ANY_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()[:max_chars]


# ── WebCrawler ───────────────────────────────────────────────────────────


class WebCrawler:
    """crawl4ai-first crawler with httpx fallback, rate limiting, robots.

    ``crawl(url)`` returns a ``CrawlDocument`` (empty content when the page
    could not be fetched) — never raises for network errors.
    """

    def __init__(
        self,
        embedder: Any | None = None,
        store: Any | None = None,
        respect_robots: bool = True,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._respect_robots = respect_robots
        self._rate_limiter = DomainRateLimiter()
        self._robots = RobotsTxtCache()
        self._crawl4ai_module: Any | None = None
        self._crawl4ai_probed = False

    async def _services(self) -> tuple[Any, Any]:
        """Lazily build the embedder + store singletons."""
        if self._embedder is None:
            self._embedder = InfinityBGEEmbedder(
                infinity_url=os.getenv("INFINITY_URL", "http://localhost:8001")
            )
        if self._store is None:
            self._store = QdrantVectorStore()
        return self._embedder, self._store

    def _crawl4ai(self) -> Any | None:
        """Lazily import crawl4ai; returns None when unavailable."""
        if self._crawl4ai_probed:
            return self._crawl4ai_module
        self._crawl4ai_probed = True
        try:
            import crawl4ai  # type: ignore[import-not-found]

            self._crawl4ai_module = crawl4ai
            log.info(
                "web_crawler_backend=crawl4ai version=%s", getattr(crawl4ai, "__version__", "?")
            )
        except Exception as e:
            log.warning("web_crawler_backend=httpx crawl4ai_unavailable err=%s", str(e)[:120])
            self._crawl4ai_module = None
        return self._crawl4ai_module

    async def crawl(
        self,
        url: str,
        source: str = "",
        category: str = "",
        collection: str = "default",
        published: str = "",
    ) -> CrawlDocument:
        """Fetch *url* and return a document with clean extracted text."""
        doc = doc_from_article(
            title="",
            content="",
            url=url,
            source=source or urlparse(url).netloc,
            collection=collection,
            category=category,
            published=published,
        )
        await self._rate_limiter.acquire(url)
        try:
            text = await self._crawl4ai_fetch(url)
            if not text:
                text = await self._httpx_fetch(url)
            doc.content = (text or "").strip()[:20000]
            doc.metadata["fetched_at"] = datetime.now(UTC).isoformat()
        except Exception as e:
            log.warning("web_crawler_crawl_failed url=%s err=%s", url, str(e)[:120])
        return doc

    async def _crawl4ai_fetch(self, url: str) -> str | None:
        """Crawl *url* with crawl4ai; return extracted markdown, or None."""
        c4 = self._crawl4ai()
        if c4 is None:
            return None
        try:
            async with c4.AsyncWebCrawler(
                config=c4.BrowserConfig(headless=True, text_mode=True, verbose=False)
            ) as crawler:
                result = await crawler.arun(
                    url=url,
                    config=c4.CrawlerRunConfig(
                        cache_mode=c4.CacheMode.BYPASS,
                        check_robots_txt=True,
                        word_count_threshold=10,
                        excluded_tags=["nav", "footer", "header", "aside", "script", "style"],
                        page_timeout=CRAWL4AI_TIMEOUT * 1000,
                        verbose=False,
                    ),
                )
                if not result.success:
                    log.debug(
                        "web_crawler_crawl4ai_failed url=%s err=%s", url, result.error_message
                    )
                    return None
                markdown = getattr(result, "markdown", None)
                if isinstance(markdown, str):
                    return markdown
                fit = getattr(markdown, "fit_markdown", None)  # MarkdownGenerationResult
                if isinstance(fit, str) and fit.strip():
                    return fit
                raw = getattr(markdown, "raw_markdown", None)
                return raw if isinstance(raw, str) else None
        except Exception as e:
            log.debug("web_crawler_crawl4ai_error url=%s err=%s", url, str(e)[:120])
            return None

    async def _httpx_fetch(self, url: str) -> str | None:
        """Plain HTTP fallback with robots.txt + rate limit + UA header."""
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": CRAWLER_USER_AGENT}
        ) as client:
            if self._respect_robots and not await self._robots.allowed(url, client):
                log.debug("web_crawler_robots_blocked url=%s", url)
                return None
            r = await client.get(url)
            if r.status_code != 200:
                log.debug("web_crawler_http_status url=%s status=%d", url, r.status_code)
                return None
            return html_to_text(r.text)

    async def crawl_many(
        self,
        urls: list[str],
        source: str = "",
        category: str = "",
        collection: str = "default",
        limit: int = 20,
        concurrency: int = 3,
    ) -> list[CrawlDocument]:
        """Crawl up to *limit* URLs with bounded concurrency."""
        sem = asyncio.Semaphore(concurrency)

        async def _one(url: str) -> CrawlDocument:
            async with sem:
                return await self.crawl(
                    url, source=source, category=category, collection=collection
                )

        docs = await asyncio.gather(*(_one(u) for u in urls[:limit]))
        return [d for d in docs if d.content.strip()]

    async def store(self, docs: list[CrawlDocument], collection: str) -> dict[str, int]:
        """Embed + upsert *docs* into *collection* in Qdrant."""
        if not docs:
            return {"inserted": 0, "failed": 0, "chunks": 0}
        embedder, store = await self._services()
        return await store_documents(docs, embedder, store)


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
    """Crawl crypto X/Twitter via SearXNG (Talos:8089) for each tracked account."""
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
