"""Crawl4AI integration for RAG ingestion.

Handles: crypto news sites, project docs, whitepapers, audit reports.

Pipeline (crawl → chunk → embed → ANN store):
  1. ``crawl_crypto_page``     — crawl a single page with Crawl4AI (JS-aware)
  2. ``crawl_and_ingest``      — crawl + ingest into the RAG engine
  3. ``discover_crypto_news``  — RSS/sitemap discovery from unified_news_sources.json
  4. ``discover_and_ingest``   — periodic sweep: discover → crawl → ingest

Run standalone (cron / manual):
    python3 -m rugbrain.ingest.crawler discover_and_ingest
    python3 -m rugbrain.ingest.crawler crawl "https://example.com"

Crawl4AI is imported lazily so importing this module never requires the
optional dependency (the RAG engine works without it; only the crawler
path needs ``pip install crawl4ai`` + Playwright browsers).

Degodding split (2026-08-13): constants moved to ``rag/_crawler_constants.py``
and the pure article helpers (spam/clickbait gates, HTML cleaning, summary,
quality/relevance/freshness scoring, Crawl4AI result unwrapping) moved to
``rag/_crawler_helpers.py``. This module is the crawl lifecycle only; every
public name is re-exported so ``crawler.<name>`` references in tests and
callers keep resolving.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from rugbrain._logging import get_logger
from rugbrain.core.dedup import (
    check_duplicates,
    content_fingerprint,
    lookup_group_by_url,
    mark_content_ingested,
    mark_url_ingested,
    new_group_id,
    record_fp_group,
    record_group_membership,
)
from rugbrain.core.ratings import rate_article
from rugbrain.ingest._crawler_constants import (
    _SOURCES_FILE,
    DEFAULT_COLLECTION,
    DEFAULT_MAX_URLS_PER_SWEEP,
    DEFAULT_PAGE_TIMEOUT_MS,
    DUP_COUNTERS,
    MAX_ARTICLE_AGE_HOURS,
    MAX_ARTICLES_PER_SOURCE,
    MIN_ARTICLE_QUALITY,
)
from rugbrain.ingest._crawler_helpers import (  # noqa: F401  (re-exported for callers)
    _article_domain,
    _BoilerplateStripper,
    _doc_id,
    _extract_markdown,
    _normalise_whitespace,
    _unwrap_result,
    clean_article_html,
    clean_title,
    filter_relevant,
    filter_spam,
    freshness_tier,
    generate_summary,
    is_english,
    is_fresh,
    is_relevant,
    is_spam_or_clickbait,
    load_sources,
    prioritize_by_freshness,
    score_article_quality,
)
from rugbrain.settings import settings

log = get_logger(__name__)


# ── Crawl ───────────────────────────────────────────────────────────────────


async def crawl_crypto_page(url: str) -> dict[str, Any]:
    """Crawl a single crypto-related page.

    Args:
        url: The page to crawl.

    Returns:
        ``{"title", "content", "metadata"}`` where ``content`` is raw
        markdown and ``metadata`` includes the source URL, crawl time,
        and the crawler name.

    Raises:
        RuntimeError: If Crawl4AI is unavailable, the crawl fails, or the
            page yields no extractable content.
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "Crawl4AI is not installed — run `pip install crawl4ai` and `playwright install chromium` before crawling"
        ) from e

    browser = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig(page_timeout=DEFAULT_PAGE_TIMEOUT_MS)

    async with AsyncWebCrawler(config=browser) as crawler:
        container = await crawler.arun(url=url, config=run_config)
        result = _unwrap_result(container)

    if result is None:
        raise RuntimeError(f"crawl returned no result for {url}")

    if not getattr(result, "success", False):
        err = getattr(result, "error_message", "unknown crawl error")
        raise RuntimeError(f"crawl failed for {url}: {err}")

    content = _extract_markdown(result)
    if not content or not content.strip():
        # Fall back to extracted content (non-markdown renderers). Crawl4AI
        # returns HTML here — strip cookie/newsletter/sidebar boilerplate so
        # only the article body is ingested.
        content = clean_article_html(str(getattr(result, "extracted_content", "") or ""))
    else:
        # Markdown path: normalise whitespace, keep paragraph breaks.
        content = _normalise_whitespace(content)

    meta = dict(getattr(result, "metadata", None) or {})
    # Crawl4AI titles often carry a site suffix ("Headline | CoinDesk").
    raw_title = str(meta.get("title") or "")[:500]
    title = clean_title(raw_title, _article_domain(url))[:500]

    return {
        "title": title,
        "content": content.strip(),
        "metadata": {
            "source_url": url,
            "crawled_at": datetime.now(UTC).isoformat(),
            "crawler": "crawl4ai",
            "title": title,
            "status_code": getattr(result, "status_code", None),
        },
    }


async def crawl_and_ingest(
    url: str,
    collection: str = DEFAULT_COLLECTION,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
    published_at: str = "",
) -> dict[str, Any]:
    """Crawl a page and ingest it directly into the RAG engine.

    Multi-level dedup (all fail-open — backend outages never block):
      1. URL hash match            → ``url_dup``
      2. Title similarity (Qdrant) → ``title_dup``
      3. Content fingerprint       → ``content_dup``
      4. Crypto entity overlap     → ``entity_dup``

    On a duplicate the article is NOT ingested; the result carries
    ``status="duplicate"``, ``duplicate_level``, and the existing story's
    ``group_id`` (CryptoPanic-style grouping). Otherwise the article is
    ingested with a ``group_id`` in its metadata linking story variations.

    Args:
        url: The page to crawl and ingest.
        collection: RAG collection to write into (default ``news_v2``).
        content: Pre-extracted content. When supplied (e.g. after a quality
            check in ``discover_and_ingest``) the crawl is skipped and this
            content is ingested — avoids crawling the same page twice.
        metadata: Pre-extracted metadata paired with ``content``. Defaults
            to fresh crawl metadata.
        published_at: ISO/RSS publish timestamp (used by entity-level dedup
            time window). Empty when unknown.

    Returns:
        ``{"doc_id", "chunks", "status", "duplicate_level", "group_id"}``.
        ``duplicate_level`` is one of url|title|content|entity (or None).
    """
    from rugbrain.core.engine import ingest_document

    if content is None:
        page = await crawl_crypto_page(url)
        content = page["content"]
        metadata = page["metadata"]
    elif metadata is None:
        metadata = {
            "source_url": url,
            "crawled_at": datetime.now(UTC).isoformat(),
            "crawler": "crawl4ai",
            "title": url,
        }

    title = str(metadata.get("title") or "")[:500]
    result = await check_duplicates(
        url=url, title=title, content=content, collection=collection, published_at=published_at
    )
    fp = content_fingerprint(content)

    if result.is_duplicate:
        group_id = result.group_id or lookup_group_by_url(url)
        log.debug(
            "dedup_decision level=%s url=%s group=%s matched=%s",
            result.level,
            url,
            group_id,
            result.matched_doc_id,
        )
        return {
            "doc_id": _doc_id(url),
            "chunks": 0,
            "status": "duplicate",
            "duplicate_level": result.level,
            "group_id": group_id,
        }

    # Fresh story: persist the group mapping so variations link to it.
    group_id = result.group_id or new_group_id(url)
    record_group_membership(group_id, url, collection)
    record_fp_group(fp, group_id, collection)

    ingest_metadata = {**metadata, "group_id": group_id}
    if published_at:
        ingest_metadata.setdefault("published_at", published_at)
    # RSS summaries are often empty or boilerplate — generate one from the
    # extracted body so downstream consumers always have a useful blurb.
    if not ingest_metadata.get("summary"):
        ingest_metadata["summary"] = generate_summary(content)[:1000]

    # Multi-axis rating card (Ground News style) attached to metadata so
    # every ingested article carries factuality / source reputation /
    # market impact / sentiment / timeliness. Ratings never block
    # ingestion — a failure degrades to metadata without the card.
    try:
        rating = rate_article(
            {
                "article_id": url,
                "title": title,
                "content": content,
                "source": str(metadata.get("source_url") or url),
                "published": published_at,
            }
        )
        ingest_metadata["rating"] = rating.to_dict()
        ingest_metadata["rating_confidence"] = rating.confidence
    except Exception as e:  # pragma: no cover - defensive
        log.debug("rating_failed url=%s err=%s", url, e)

    result_ingest = await ingest_document(
        collection=collection,
        doc_id=_doc_id(url),
        content=content,
        metadata=ingest_metadata,
    )
    if result_ingest.get("status") == "ok":
        mark_url_ingested(url)
        mark_content_ingested(fp, collection)
    return {
        "doc_id": result_ingest.get("doc_id", _doc_id(url)),
        "chunks": result_ingest.get("chunks", 0),
        "status": result_ingest.get("status", "unknown"),
        "duplicate_level": None,
        "group_id": group_id,
    }


# ── Discovery ───────────────────────────────────────────────────────────────


async def discover_crypto_news(sources: list[str] | None = None) -> list[dict[str, Any]]:
    """Discover crypto content from RSS feeds / sitemaps.

    Args:
        sources: Explicit feed URLs. Defaults to all feeds in
            ``unified_news_sources.json`` (rss/atom/sitemap/reddit/nitter
            types — the reddit/nitter entries are RSS feeds too).

    Returns:
        A list of discovered items: ``{"url", "title", "published",
        "source", "summary"}``. Feeds that fail to parse are logged and
        skipped (never fatal).
    """
    try:
        import feedparser  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError("feedparser is required for RSS discovery") from e

    if sources is not None:
        feed_urls = list(dict.fromkeys(sources))
    else:
        feed_urls = list(dict.fromkeys(u for urls in load_sources().values() for u in urls))
    if not feed_urls:
        log.warning("discover_no_sources", hint=f"add feeds to {_SOURCES_FILE}")
        return []

    items: list[dict[str, Any]] = []
    for feed_url in feed_urls:
        try:
            parsed = await asyncio.to_thread(feedparser.parse, feed_url)
        except Exception as e:
            log.warning("discover_feed_failed", feed=feed_url, error=str(e))
            continue

        if parsed.bozo and not parsed.entries:
            log.warning("discover_feed_bozo", feed=feed_url, error=getattr(parsed, "bozo_exception", None))
            continue

        # Per-source rate limiting: some RSS feeds churn every minute with
        # repeat content — cap the number of items taken per feed per sweep.
        for entry in parsed.entries[:MAX_ARTICLES_PER_SOURCE]:
            link = str(getattr(entry, "link", "") or "").strip()
            if not link.startswith(("http://", "https://")):
                continue
            items.append(
                {
                    "url": link,
                    # RSS titles frequently carry a site suffix (" | CoinDesk").
                    "title": clean_title(str(getattr(entry, "title", "") or "")[:500], _article_domain(link)),
                    "published": str(getattr(entry, "published", "") or ""),
                    "source": feed_url,
                    "summary": str(getattr(entry, "summary", "") or "")[:1000],
                }
            )

    return items


# ── Sweep (cron entrypoint) ─────────────────────────────────────────────────


def _hostname_resolves(hostname: str) -> bool:
    """Best-effort DNS check for a hostname (no raise)."""
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except OSError:
        return False


def _ensure_host_connectivity() -> None:
    """Pre-flight host-side connectivity for cron runs (executed OUTSIDE Docker).

    When the embedding service is only reachable by a container-internal
    name that does not resolve from this process (e.g. a host-side cron
    run), the sweep fails open instead of failing hard.

    This makes the sweep fail open instead of failing hard:

    * ``INFINITY_URL`` falls back to the host-mapped port (``localhost:8001``)
      when the configured hostname does not resolve from this process.
    * Missing ``REDIS_URL`` (no credentials available) is logged and dedup is
      skipped downstream — ingestion still proceeds.

    Never raises: connectivity problems are logged and the sweep continues.
    """
    if not os.getenv("REDIS_URL"):
        log.warning(
            "redis_url_missing host_side dedup_will_be_skipped hint=set REDIS_URL=redis://:password@localhost:6379/0"
        )

    infinity = os.getenv("INFINITY_URL") or settings.infinity_url
    try:
        host = urlparse(infinity).hostname or ""
    except ValueError:
        host = ""
    if host and not _hostname_resolves(host):
        fallback = os.getenv("INFINITY_URL_FALLBACK", "http://localhost:8001")
        log.warning("infinity_dns_fallback from=%s to=%s", infinity, fallback)
        os.environ["INFINITY_URL"] = fallback
        # The settings singleton was built at import time; align it so the
        # lazy embedder singleton picks up the reachable URL.
        with contextlib.suppress(Exception):  # best-effort; env var is the source of truth
            settings.infinity_url = fallback


async def discover_and_ingest(
    max_urls: int = DEFAULT_MAX_URLS_PER_SWEEP,
    collection: str = DEFAULT_COLLECTION,
    sources: list[str] | None = None,
    min_quality: float = MIN_ARTICLE_QUALITY,
    max_age_hours: float = MAX_ARTICLE_AGE_HOURS,
) -> dict[str, Any]:
    """Discover new crypto content and ingest it into RAG.

    This is the cron entrypoint used by ``crawl-crypto-news.sh``. Each
    discovered URL is crawled and ingested independently so a single
    failure does not abort the sweep.

    Quality pipeline per sweep:
      1. Spam/clickbait gate — drop promotional domains and clickbait
         titles (``is_spam_or_clickbait``) before anything else.
      2. Relevance gate (title + summary) — drop off-topic articles
         (NFT drops, pure gaming, general tech) before crawling.
      3. Freshness prioritisation — newest first; ``> max_age_hours``
         old articles are skipped as stale.
      4. Language gate — non-English extracted content is skipped
         (we cannot rate it properly).
      5. Quality score on extracted content — articles scoring below
         ``min_quality`` are skipped.
    """
    _ensure_host_connectivity()
    discovered = await discover_crypto_news(sources)
    pre_spam = len(discovered)
    spam_kept = filter_spam(discovered)
    spam_filtered = pre_spam - len(spam_kept)
    relevant = filter_relevant(spam_kept)
    prioritized = prioritize_by_freshness(relevant)

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    stale_skipped: list[str] = []
    non_english_skipped: list[str] = []
    quality_rejected: list[dict[str, Any]] = []

    for item in prioritized[:max_urls]:
        url = item["url"]
        if not is_fresh(item, max_age_hours=max_age_hours):
            stale_skipped.append(url)
            log.info("sweep_item_stale", url=url, published=item.get("published"))
            continue

        try:
            page = await crawl_crypto_page(url)
        except Exception as e:
            errors.append(f"{url}: {e}")
            log.warning("sweep_item_failed", url=url, error=str(e))
            continue

        # Non-English articles are skipped — the ratings engine cannot rate
        # them properly (vocabularies are English-only).
        if not is_english(page["content"]):
            non_english_skipped.append(url)
            log.info("sweep_item_non_english", url=url)
            continue

        article: dict[str, Any] = {**item, "content": page["content"]}
        quality = score_article_quality(article)
        if quality < min_quality:
            quality_rejected.append({"url": url, "quality": round(quality, 3)})
            log.info("sweep_item_low_quality", url=url, quality=round(quality, 3))
            continue

        try:
            ingested = await crawl_and_ingest(
                url,
                collection=collection,
                content=page["content"],
                metadata=page["metadata"],
                published_at=item.get("published") or "",
            )
            results.append({"url": url, **ingested})
        except Exception as e:
            errors.append(f"{url}: {e}")
            log.warning("sweep_item_failed", url=url, error=str(e))

    tier_counts = {"fresh": 0, "recent": 0, "stale": 0, "unknown": 0}
    for item in prioritized:
        tier = freshness_tier(item)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    dup_levels = dict.fromkeys(DUP_COUNTERS, 0)
    for r in results:
        level = r.get("duplicate_level")
        counter = f"{level}_dup" if level else None
        if counter in dup_levels:
            dup_levels[counter] += 1

    summary = {
        "collection": collection,
        "discovered": len(discovered),
        "spam_filtered": spam_filtered,
        "relevant": len(relevant),
        "freshness_tiers": tier_counts,
        "attempted": len(results) + len(errors),
        "ingested": sum(1 for r in results if r.get("status") == "ok"),
        "duplicates": sum(1 for r in results if r.get("status") == "duplicate"),
        **dup_levels,
        "stale_skipped": len(stale_skipped),
        "non_english_skipped": len(non_english_skipped),
        "quality_rejected": len(quality_rejected),
        "failed": len(errors),
        "errors": errors[:20],
        "ran_at": datetime.now(UTC).isoformat(),
    }
    log.info("crawl_sweep_complete", **summary)
    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    """CLI entrypoint: ``python -m app.rag.crawler <subcommand>``."""
    parser = argparse.ArgumentParser(prog="python -m rugbrain.ingest.crawler", description="Crawl4AI RAG ingestion")
    sub = parser.add_subparsers(dest="command")

    sweep = sub.add_parser("discover_and_ingest", help="Discover + crawl + ingest (cron entrypoint)")
    sweep.add_argument("--max-urls", type=int, default=DEFAULT_MAX_URLS_PER_SWEEP)
    sweep.add_argument("--collection", default=DEFAULT_COLLECTION)
    sweep.add_argument("--feed", action="append", default=None, help="Extra feed URL (repeatable)")
    sweep.add_argument(
        "--min-quality", type=float, default=MIN_ARTICLE_QUALITY, help="Skip articles below this quality score (0-1)"
    )
    sweep.add_argument(
        "--max-age-hours", type=float, default=MAX_ARTICLE_AGE_HOURS, help="Skip articles older than this many hours"
    )

    single = sub.add_parser("crawl", help="Crawl a single URL and print the result")
    single.add_argument("url")
    single.add_argument("--collection", default=None, help="If set, also ingest")

    args = parser.parse_args()

    if args.command == "crawl":

        async def _single() -> None:
            page = await crawl_crypto_page(args.url)
            print(json.dumps(page, indent=2, default=str))
            if args.collection:
                print(json.dumps(await crawl_and_ingest(args.url, collection=args.collection), indent=2))

        asyncio.run(_single())
        return

    # Default (and discover_and_ingest): run the sweep.
    asyncio.run(
        discover_and_ingest(
            max_urls=args.max_urls,
            collection=args.collection,
            sources=args.feed,
            min_quality=args.min_quality,
            max_age_hours=args.max_age_hours,
        )
    )


if __name__ == "__main__":
    main()
