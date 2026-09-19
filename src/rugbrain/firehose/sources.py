"""FeedSources — the firehose's source facade + default source registry.

Delegates to source-specific modules (news, twitter, chain_sources).
"""

import logging
from typing import Any

import httpx

from . import chain_sources
from .models import FeedSource
from .news import pull_news_rss as _pull_news_rss
from .news import pull_prediction_markets as _pull_prediction_markets
from .twitter import pull_ct_rundown as _pull_ct_rundown
from .twitter import pull_social_sentiment as _pull_social_sentiment

logger = logging.getLogger("rag.firehose")

__all__ = ["FeedSources", "build_default_sources"]


class FeedSources:
    """All data sources the firehose can pull from.

    Delegates to source-specific modules for twitter, news, etc.
    Chain-data sources (etherscan, solana, pattern extraction) delegate to
    ``chain_sources``.
    """

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    # ── Hourly: News (delegated) ──

    async def pull_news_rss(self) -> list[dict[str, Any]]:
        return await _pull_news_rss(self.client)

    async def pull_ct_rundown(self) -> list[dict[str, Any]]:
        return await _pull_ct_rundown(self.client)

    async def pull_social_sentiment(self) -> list[dict[str, Any]]:
        return await _pull_social_sentiment(self.client)

    async def pull_prediction_markets(self) -> list[dict[str, Any]]:
        return await _pull_prediction_markets(self.client)

    # ── Daily: Scam Databases (delegated to chain_sources) ──

    async def pull_etherscan_labels(self) -> list[dict[str, Any]]:
        return await chain_sources.pull_etherscan_labels(self.client)

    async def pull_solana_scams(self) -> list[dict[str, Any]]:
        return await chain_sources.pull_solana_scams(self.client)

    # ── Weekly: Pattern Extraction (delegated) ──

    async def extract_scam_patterns(self) -> list[dict[str, Any]]:
        return await chain_sources.extract_scam_patterns(self.client)


def build_default_sources(feeds: FeedSources) -> list[FeedSource]:
    """Build the default firehose source registry (same order as before)."""
    return [
        FeedSource(
            "ct_rundown",
            "news_articles",
            900,
            feeds.pull_ct_rundown,
            True,
            "CT Rundown stories",
        ),
        FeedSource(
            "social_sentiment",
            "social_sentiment",
            900,
            feeds.pull_social_sentiment,
            True,
            "Social sentiment and scam alerts",
        ),
        FeedSource(
            "news_rss",
            "news_articles",
            3600,
            feeds.pull_news_rss,
            True,
            "200+ RSS crypto news feeds",
        ),
        FeedSource(
            "prediction_markets",
            "market_intel",
            21600,
            feeds.pull_prediction_markets,
            True,
            "Polymarket predictions",
        ),
        FeedSource(
            "etherscan_labels",
            "known_scams",
            86400,
            feeds.pull_etherscan_labels,
            True,
            "Etherscan phish/hack labeled addresses",
        ),
        FeedSource(
            "solana_scams",
            "known_scams",
            86400,
            feeds.pull_solana_scams,
            True,
            "Solana token registry flagged tokens",
        ),
        FeedSource(
            "scam_patterns",
            "scam_patterns",
            259200,
            feeds.extract_scam_patterns,
            True,
            "Extract common patterns from confirmed scams",
        ),
    ]
