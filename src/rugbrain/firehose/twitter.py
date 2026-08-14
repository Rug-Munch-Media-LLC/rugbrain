"""
Firehose — Twitter/X data sources.

Extracted from firehose.py FeedSources class.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger("rag.firehose")


async def pull_ct_rundown(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull CT Rundown stories (crypto Twitter)."""
    docs: list[dict[str, Any]] = []
    try:
        resp = await client.get(
            "http://localhost:8000/api/v1/databus/fetch/ct_rundown",
            params={"limit": 10},
            timeout=30,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        stories = data.get("stories", data.get("data", []))

        for story in (stories if isinstance(stories, list) else [])[:5]:
            content = f"CT: {story.get('title', '')} - {story.get('summary', '')}"
            docs.append(
                {
                    "collection": "news_articles",
                    "content": content[:1500],
                    "metadata": {
                        "source": "ct_rundown",
                        "category": "crypto_twitter",
                        "handle": story.get("handle", ""),
                        "engagement": story.get("engagement", 0),
                    },
                }
            )
    except Exception as e:
        logger.warning("CT rundown pull failed: %s", e)

    return docs


async def pull_social_sentiment(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull social sentiment and scam alerts from Twitter/social sources."""
    docs: list[dict[str, Any]] = []
    try:
        # Scam monitor
        resp = await client.get(
            "http://localhost:8000/api/v1/databus/fetch/scam_monitor", timeout=20
        )
        if resp.status_code == 200:
            data = resp.json()
            alerts = data.get("alerts", data.get("data", []))
            for alert in (alerts if isinstance(alerts, list) else [])[:10]:
                docs.append(
                    {
                        "collection": "social_sentiment",
                        "content": f"Scam alert: {alert.get('title', '')} - {alert.get('description', '')}",
                        "metadata": {
                            "source": "scam_monitor",
                            "severity": alert.get("severity", "medium"),
                            "token": alert.get("token_address", ""),
                            "chain": alert.get("chain", ""),
                        },
                    }
                )

        # Social metrics
        resp2 = await client.get(
            "http://localhost:8000/api/v1/databus/fetch/social_metrics", timeout=20
        )
        if resp2.status_code == 200:
            data2 = resp2.json()
            metrics = data2.get("metrics", data2.get("data", {}))
            if metrics:
                docs.append(
                    {
                        "collection": "social_sentiment",
                        "content": f"Social metrics: Sentiment {metrics.get('sentiment', '?')}, "
                        f"Trending: {metrics.get('trending_topics', [])}",
                        "metadata": {"source": "social_metrics", "type": "daily_summary"},
                    }
                )
    except Exception as e:
        logger.warning("Social pull failed: %s", e)

    return docs
