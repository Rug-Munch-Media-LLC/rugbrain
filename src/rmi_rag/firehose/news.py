"""
Firehose — News and market data sources.

Extracted from firehose.py FeedSources class.
"""

import logging

logger = logging.getLogger("rag.firehose")


async def pull_news_rss(client) -> list[dict]:
    """Pull latest news from DataBus news provider (200+ RSS feeds)."""
    docs = []
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

    return docs


async def pull_prediction_markets(client) -> list[dict]:
    """Pull Polymarket prediction data for market intel."""
    docs = []
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

    return docs
