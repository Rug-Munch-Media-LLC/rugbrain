"""Additional RugBrain data sources — news, education, research, standards.

Adds ten ingestion pipelines beyond the core crawl4ai sources:

  * ``cryptopanic``      — news aggregator API (free tier, token optional)
  * ``cmc_alexandria``   — CoinMarketCap Alexandria educational content
  * ``messari``          — Messari Research free-tier reports
  * ``bankless``         — Bankless newsletter archive
  * ``the_defiant``      — DeFi news
  * ``week_in_ethereum`` — Week in Ethereum newsletter
  * ``solana_compass``   — Solana guides
  * ``eigenlayer``       — EigenLayer blog (restaking / AVS)
  * ``eips_ethereum``    — EIP/ERC standards on eips.ethereum.org
  * ``bitcoin_optech``   — Bitcoin Optech technical content

Each source declares its preferred transport (``rss``, ``api``, ``crawl``,
or ``rss_or_crawl``). RSS/API sources are fetched directly; everything else
falls back to the crawl4ai-backed ``WebCrawler`` for clean text extraction.
All documents are embedded and stored in Qdrant with deterministic IDs.

Usage::

    python -m rugbrain.sources_registry --all --limit 15
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from rugbrain.bge_embedders import InfinityBGEEmbedder
from rugbrain.ingest_common import CrawlDocument, doc_from_article, store_documents
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.web_crawler import (
    CRAWLER_USER_AGENT,
    HTTP_TIMEOUT,
    WebCrawler,
    fetch_rss_feed,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceConfig:
    """Declarative config for a data source pipeline.

    ``kind`` selects the transport:
      * ``"rss"``         — parse an RSS/Atom feed (first URL)
      * ``"api"``         — call a JSON API (``auth_env`` optional token)
      * ``"crawl"``       — crawl each URL with crawl4ai/WebCrawler
      * ``"rss_or_crawl"``— try RSS at URL[0], crawl remaining URLs on failure
    """

    name: str
    collection: str
    category: str
    kind: str
    urls: tuple[str, ...]
    auth_env: str = ""
    extra: dict[str, str] = field(default_factory=dict)


NEW_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        "cryptopanic",
        "news_articles",
        "news_aggregator",
        "api",
        ("https://cryptopanic.com/api/v1/posts/",),
        auth_env="CRYPTOPANIC_API_TOKEN",
        extra={"fallback_url": "https://cryptopanic.com/news/"},
    ),
    SourceConfig(
        "cmc_alexandria",
        "education",
        "educational_guide",
        "crawl",
        ("https://coinmarketcap.com/alexandria/",),
    ),
    SourceConfig(
        "messari",
        "research",
        "research_report",
        "crawl",
        ("https://messari.io/reports",),
    ),
    SourceConfig(
        "bankless",
        "education",
        "newsletter",
        "rss_or_crawl",
        ("https://www.bankless.com/feed", "https://www.bankless.com/archive"),
    ),
    SourceConfig(
        "the_defiant",
        "news_articles",
        "defi_news",
        "rss_or_crawl",
        ("https://thedefiant.io/feed.xml", "https://thedefiant.io/news"),
    ),
    SourceConfig(
        "week_in_ethereum",
        "news_articles",
        "newsletter",
        "rss",
        ("https://weekinethereumnews.com/feed/",),
    ),
    SourceConfig(
        "solana_compass",
        "education",
        "solana_guide",
        "crawl",
        ("https://solanacompass.com",),
    ),
    SourceConfig(
        "eigenlayer",
        "education",
        "restaking",
        "rss_or_crawl",
        ("https://blog.eigenlayer.xyz/rss.xml", "https://blog.eigenlayer.xyz"),
    ),
    SourceConfig(
        "eips_ethereum",
        "standards",
        "eip_standard",
        "crawl",
        ("https://eips.ethereum.org/all",),
    ),
    SourceConfig(
        "bitcoin_optech",
        "education",
        "bitcoin_technical",
        "rss",
        ("https://bitcoinops.org/feed.xml",),
    ),
)


def get_source(name: str) -> SourceConfig | None:
    """Return the source config with *name*, or None."""
    for cfg in NEW_SOURCES:
        if cfg.name == name:
            return cfg
    return None


# ── Transport-specific fetchers ──────────────────────────────────────────


async def _fetch_cryptopanic(
    cfg: SourceConfig, client: httpx.AsyncClient, limit: int
) -> list[CrawlDocument]:
    """CryptoPanic news API (free tier). Falls back to the news page."""
    token = os.getenv(cfg.auth_env, "").strip()
    docs: list[CrawlDocument] = []
    if not token:
        log.warning("cryptopanic_no_token set %s for API; crawling news page", cfg.auth_env)
        return docs
    try:
        r = await client.get(
            cfg.urls[0],
            params={"auth_token": token, "public": "true", "kind": "news", "limit": limit},
        )
        if r.status_code != 200:
            log.debug("cryptopanic_status status=%d", r.status_code)
            return docs
        posts = (r.json() or {}).get("results") or []
        for post in posts[:limit]:
            if not isinstance(post, dict):
                continue
            title = str(post.get("title", "")).strip()
            if not title:
                continue
            votes = post.get("votes") or {}
            docs.append(
                doc_from_article(
                    title=title[:300],
                    content=title,
                    url=str(post.get("url", "")),
                    source="cryptopanic",
                    collection=cfg.collection,
                    category=cfg.category,
                    published=str(post.get("published_at", ""))[:10],
                    metadata={
                        "domain": str(post.get("domain", "")),
                        "upvotes": int(votes.get("positive", 0)),
                        "downvotes": int(votes.get("negative", 0)),
                    },
                )
            )
    except Exception as e:
        log.warning("cryptopanic_fetch_failed err=%s", str(e)[:120])
    return docs


async def fetch_source(
    cfg: SourceConfig,
    crawler: WebCrawler | None = None,
    client: httpx.AsyncClient | None = None,
    limit: int = 15,
) -> list[CrawlDocument]:
    """Fetch documents for *cfg* using its declared transport."""
    c = crawler or WebCrawler()
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": CRAWLER_USER_AGENT}
    )
    try:
        if cfg.kind == "rss":
            docs: list[CrawlDocument] = []
            for url in cfg.urls:
                docs.extend(
                    await fetch_rss_feed(url, cfg.name, cfg.collection, cfg.category, limit)
                )
            return docs[:limit]
        if cfg.kind == "api":
            return await _fetch_cryptopanic(cfg, http, limit)
        if cfg.kind == "rss_or_crawl":
            docs = await fetch_rss_feed(cfg.urls[0], cfg.name, cfg.collection, cfg.category, limit)
            if docs:
                return docs[:limit]
            return await _crawl_urls(cfg, c, offset=1, limit=limit)
        # crawl (default)
        return await _crawl_urls(cfg, c, offset=0, limit=limit)
    finally:
        if own_client:
            await http.aclose()


async def _crawl_urls(
    cfg: SourceConfig, crawler: WebCrawler, offset: int = 0, limit: int = 10
) -> list[CrawlDocument]:
    docs: list[CrawlDocument] = []
    for url in cfg.urls[offset:]:
        doc = await crawler.crawl(
            url, source=cfg.name, category=cfg.category, collection=cfg.collection
        )
        if doc.content.strip():
            docs.append(doc)
    return docs[:limit]


# ── Orchestration ────────────────────────────────────────────────────────


async def ingest_all_sources(
    crawler: WebCrawler | None = None,
    dry_run: bool = False,
    limit: int = 15,
) -> dict[str, Any]:
    """Run every NEW_SOURCES pipeline and store results in Qdrant.

    Returns ``{source_name: {"inserted": n, "failed": n, "chunks": n}}``.
    In dry-run mode documents are fetched but not stored.
    """
    c = crawler or WebCrawler()
    embedder = InfinityBGEEmbedder(infinity_url=os.getenv("INFINITY_URL", "http://localhost:8001"))
    store = QdrantVectorStore()
    totals: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": CRAWLER_USER_AGENT}
        ) as client:
            for cfg in NEW_SOURCES:
                try:
                    docs = await fetch_source(cfg, crawler=c, client=client, limit=limit)
                except Exception as e:
                    log.warning("source_fetch_failed name=%s err=%s", cfg.name, str(e)[:120])
                    totals[cfg.name] = {
                        "inserted": 0,
                        "failed": 0,
                        "chunks": 0,
                        "error": str(e)[:120],
                    }
                    continue
                if dry_run:
                    totals[cfg.name] = {"inserted": len(docs), "failed": 0, "chunks": len(docs)}
                    log.info("source_dry_run name=%s docs=%d", cfg.name, len(docs))
                    continue
                if not docs:
                    totals[cfg.name] = {"inserted": 0, "failed": 0, "chunks": 0}
                    continue
                result = await store_documents(docs, embedder, store)
                totals[cfg.name] = result
                log.info("source_done name=%s %s", cfg.name, result)
    finally:
        await store.close()
    return totals


# ── CLI ──────────────────────────────────────────────────────────────────


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RugBrain additional data sources")
    parser.add_argument("--all", action="store_true", help="ingest all sources")
    parser.add_argument("--source", type=str, default=None, help="ingest one source by name")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true", help="fetch only, don't store")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    async def run() -> None:
        c = WebCrawler()
        if args.source:
            cfg = get_source(args.source)
            if cfg is None:
                print(f"unknown source: {args.source}; available: {[s.name for s in NEW_SOURCES]}")
                return
            docs = await fetch_source(cfg, crawler=c, limit=args.limit)
            print(f"{cfg.name}: fetched {len(docs)} docs")
            if not args.dry_run and docs:
                embedder = InfinityBGEEmbedder(
                    infinity_url=os.getenv("INFINITY_URL", "http://localhost:8001")
                )
                store = QdrantVectorStore()
                try:
                    result = await store_documents(docs, embedder, store)
                    print(f"{cfg.name}: {result}")
                finally:
                    await store.close()
            return
        if args.all:
            totals = await ingest_all_sources(c, dry_run=args.dry_run, limit=args.limit)
            print(f"SOURCES INGEST COMPLETE: {totals}")
            return
        parser.print_help()

    asyncio.run(run())


if __name__ == "__main__":
    _main()


__all__ = [
    "NEW_SOURCES",
    "SourceConfig",
    "fetch_source",
    "get_source",
    "ingest_all_sources",
]
