"""
Firehose — News and market data sources (standalone, no rmi-backend).

Extracted from firehose.py FeedSources class.

RugBrain is a standalone RAG engine: these sources fetch their primary
upstreams DIRECTLY (see ``direct_sources``) — there is no rmi-backend
DataBus call. See docs/MODERNIZATION.md §5 (backend-RAG cutaway).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("rag.firehose")


async def pull_news_rss(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Pull the latest crypto news straight from official RSS feeds.

    ``client`` is accepted for call-site compatibility but is intentionally
    unused — the direct adapters open their own short-lived connections.
    """
    from rugbrain.firehose.direct_sources import fetch_news_direct

    try:
        docs = await fetch_news_direct(limit=20)
        if docs:
            logger.info("news pulled via DIRECT rss: %d docs", len(docs))
        return docs
    except Exception as e:  # fail open: a feed outage must not stop the firehose
        logger.warning("direct news fetch failed: %s", e)
        return []


async def pull_prediction_markets(
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Pull active Polymarket markets directly from the public gamma API.

    ``client`` is accepted for call-site compatibility but is intentionally
    unused — the direct adapter opens its own short-lived connection.
    """
    from rugbrain.firehose.direct_sources import fetch_prediction_markets_direct

    try:
        docs = await fetch_prediction_markets_direct(limit=10)
        if docs:
            logger.info("prediction markets pulled via DIRECT: %d docs", len(docs))
        return docs
    except Exception as e:  # fail open
        logger.warning("direct prediction-market fetch failed: %s", e)
        return []
