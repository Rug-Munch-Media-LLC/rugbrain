"""The crawl4ai-first :class:`WebCrawler` with an httpx fallback.

Moved verbatim from ``web_crawler.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from rugbrain.ingest_common import CrawlDocument, doc_from_article, store_documents
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder
from rugbrain.web_crawler_pkg.config import (
    CRAWL4AI_TIMEOUT,
    CRAWLER_USER_AGENT,
    HTTP_TIMEOUT,
)
from rugbrain.web_crawler_pkg.html_text import html_to_text
from rugbrain.web_crawler_pkg.politeness import DomainRateLimiter, RobotsTxtCache

log = logging.getLogger(__name__)

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
            self._embedder = Qwen3Embedder(
                embed_url=os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1")
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
            import crawl4ai

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
