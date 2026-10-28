"""Crawler configuration: env knobs, sources, feeds and search queries.

Moved verbatim from the 637-line ``web_crawler.py``. Every value is still an
``os.getenv`` read at import time, exactly as before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

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
