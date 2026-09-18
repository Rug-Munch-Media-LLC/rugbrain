"""
Firehose — Twitter/X data sources (standalone, no rmi-backend).

Extracted from firehose.py FeedSources class.

RugBrain fetches its social sources DIRECTLY (see ``direct_sources``); there
is no rmi-backend DataBus call. See docs/MODERNIZATION.md §5.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("rag.firehose")


async def pull_ct_rundown(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """CT Rundown — gracefully degraded, NO standalone upstream identified.

    [REVIEW] docs/MODERNIZATION.md §7 Q7: the former rmi-backend
    ``/databus/fetch/ct_rundown`` aggregator wrapped a Nitter/X source that
    was never exported, and there is no free, reliable replacement (Nitter
    instances are unstable; X is paywalled). Rather than fetch from
    rmi-backend or invent a source, this source logs and skips. It stays
    registered so the firehose reports the gap instead of silently dropping.
    """
    logger.info("ct_rundown skipped: no standalone upstream identified [REVIEW]")
    return []


async def pull_social_sentiment(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Pull a standalone social-sentiment summary (Fear & Greed index).

    The former rmi-backend scam_monitor/social_metrics calls are gone; the
    direct adapter is the only path. ``client`` is accepted for call-site
    compatibility but is intentionally unused.
    """
    from rugbrain.firehose.direct_sources import fetch_social_sentiment_direct

    try:
        docs = await fetch_social_sentiment_direct()
        if docs:
            logger.info("social sentiment pulled via DIRECT: %d docs", len(docs))
        return docs
    except Exception as e:  # fail open, never stop the firehose
        logger.warning("direct social-sentiment fetch failed: %s", e)
        return []
