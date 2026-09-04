"""
Firehose — News and market data sources.

Extracted from firehose.py FeedSources class.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger("rag.firehose")


async def pull_news_rss(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull latest news from DataBus news provider (200+ RSS feeds).

    Standalone fallback (2026-08-25): when the backend DataBus is unreachable
    or returns nothing, fetch the primary upstream RSS feeds directly —
    RugBrain must ingest without rmi-backend being up.
    """
    docs: list[dict[str, Any]] = []
    try:
        resp = await client.get(
            "http://localhost:8000/api/v1/databus/fetch/news", params={"limit": 30}, timeout=30
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        articles = data.get("articles", data.get("data", []))

        for article in (articles if isinstance(articles, list) else [])[:20]:
            title = article.get("title", "")
            desc = article.get("description", article.get("summary", ""))
            if not title:
                continue
            content = f"News: {title}. {desc}"
            docs.append(
                {
                    "collection": "news_articles",
                    "content": content[:2000],
                    "metadata": {
                        "source": article.get("source", "news_rss"),
                        "url": article.get("url", ""),
                        "published": article.get("published_at", article.get("date", "")),
                        "category": article.get("category", "crypto"),
                        "title": title,
                    },
                }
            )
    except Exception as e:
        logger.warning("News RSS pull failed: %s", e)

    if not docs:
        # Backend down / empty → direct upstream adapters (standalone mode).
        try:
            from rugbrain.firehose.direct_sources import fetch_news_direct

            docs = await fetch_news_direct(limit=20)
            if docs:
                logger.info("news pulled via DIRECT rss fallback: %d docs", len(docs))
        except Exception as e:
            logger.warning("direct news fallback failed: %s", e)

    return docs


async def pull_prediction_markets(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull Polymarket prediction data for market intel."""
    docs: list[dict[str, Any]] = []
    try:
        resp = await client.get(
            "http://localhost:8000/api/v1/databus/fetch/prediction_markets", timeout=20
        )
        if resp.status_code == 200:
            data = resp.json()
            markets = data.get("markets", data.get("data", []))
            for m in (markets if isinstance(markets, list) else [])[:10]:
                docs.append(
                    {
                        "collection": "market_intel",
                        "content": f"Prediction market: {m.get('question', '')} - "
                        f"YES: {m.get('yes_price', '?')} NO: {m.get('no_price', '?')}",
                        "metadata": {"source": "polymarket", "type": "prediction"},
                    }
                )
    except Exception as e:
        logger.warning("Prediction market pull failed: %s", e)

    if not docs:
        # Backend down / empty → Polymarket gamma API directly (standalone).
        try:
            from rugbrain.firehose.direct_sources import fetch_prediction_markets_direct

            docs = await fetch_prediction_markets_direct(limit=10)
            if docs:
                logger.info("prediction markets pulled via DIRECT fallback: %d docs", len(docs))
        except Exception as e:
            logger.warning("direct prediction-market fallback failed: %s", e)

    return docs
