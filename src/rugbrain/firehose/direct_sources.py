"""Direct-fetch sources — RugBrain ingests WITHOUT rmi-backend (standalone).

The DataBus endpoints on the backend (`/api/v1/databus/fetch/*`) aggregate and
normalize these same upstreams. When the backend is down (or not deployed at
all — see docs/STANDALONE-MIGRATION-PLAN.md), the firehose falls back to
fetching the primary upstreams directly:

- news      → crypto RSS feeds (CoinDesk, Cointelegraph, Decrypt, CryptoSlate)
              parsed with stdlib xml.etree — zero new dependencies.
- prediction→ Polymarket public gamma API (no auth, free tier).

Doc shapes match the DataBus-normalized forms consumed by
``firehose.news.pull_news_rss`` / ``pull_prediction_markets`` so the fallback
is a drop-in below the primary path.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

logger = logging.getLogger("rag.firehose.direct")

# Curated, long-lived official RSS endpoints (free, no auth, no key).
_NEWS_FEEDS: tuple[tuple[str, str], ...] = (
    ("https://www.coindesk.com/arc/outboundfeeds/rss/", "coindesk"),
    ("https://cointelegraph.com/rss", "cointelegraph"),
    ("https://decrypt.co/feed", "decrypt"),
    ("https://cryptoslate.com/feed/", "cryptoslate"),
)

_POLYMARKET_GAMMA = "https://gamma-api.polymarket.com/markets"


def _text_of(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


async def fetch_news_direct(limit: int = 20) -> list[dict[str, Any]]:
    """Pull crypto news straight from official RSS feeds.

    Returns the same doc dicts ``pull_news_rss`` produces:
    ``{collection, content, metadata{source,url,published,category,title}}``.
    Per-feed failures degrade to skipping that feed — never raises.
    """
    docs: list[dict[str, Any]] = []
    per_feed = max(1, limit // len(_NEWS_FEEDS)) if _NEWS_FEEDS else 0
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        headers={"User-Agent": "rugbrain-firehose/1.0 (+https://rugmunch.io)"},
        follow_redirects=True,
    ) as client:
        for url, source in _NEWS_FEEDS:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                root = ET.fromstring(resp.text)
                items = root.findall(".//item")[:per_feed]
                for item in items:
                    title = _text_of(item.find("title"))
                    if not title:
                        continue
                    desc = _text_of(item.find("description"))
                    link = _text_of(item.find("link"))
                    pub = _text_of(item.find("pubDate"))
                    docs.append(
                        {
                            "collection": "news_articles",
                            "content": f"News: {title}. {desc}"[:2000],
                            "metadata": {
                                "source": source,
                                "url": link,
                                "published": pub,
                                "category": "crypto",
                                "title": title,
                            },
                        }
                    )
            except Exception as exc:  # noqa: BLE001 — one dead feed never kills the pull
                logger.debug("direct news feed failed source=%s err=%s", source, exc)
    return docs[:limit]


async def fetch_prediction_markets_direct(limit: int = 10) -> list[dict[str, Any]]:
    """Pull active Polymarket markets from the public gamma API (no auth).

    Mirrors the DataBus doc shape: content line + metadata.source=polymarket.
    """
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = await client.get(
                _POLYMARKET_GAMMA,
                params={"active": "true", "closed": "false", "limit": limit},
            )
        if resp.status_code != 200:
            return []
        for m in resp.json()[:limit]:
            question = str(m.get("question") or "").strip()
            if not question:
                continue
            prices = m.get("outcomePrices")
            yes_price = no_price = "?"
            if isinstance(prices, str):
                # gamma returns outcomePrices as a JSON-encoded string
                import json as _json

                try:
                    prices = _json.loads(prices)
                except ValueError:
                    prices = None
            if isinstance(prices, list) and len(prices) >= 2:
                yes_price, no_price = prices[0], prices[1]
            docs.append(
                {
                    "collection": "market_intel",
                    "content": f"Prediction market: {question} - YES: {yes_price} NO: {no_price}",
                    "metadata": {"source": "polymarket", "type": "prediction"},
                }
            )
    except Exception as exc:  # noqa: BLE001 — fail-open to empty
        logger.debug("direct polymarket fetch failed err=%s", exc)
    return docs
