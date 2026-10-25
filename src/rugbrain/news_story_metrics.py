"""Story distributions and protocol/entity topic extraction.

Split out of :mod:`rugbrain.news_clustering` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import re
from typing import Any

from rugbrain.news_cluster_core import StorySource


def _sentiment_distribution(sources: list[StorySource]) -> dict[str, int]:
    """Count sources by sentiment (positive/neutral/negative)."""
    dist = {"positive": 0, "neutral": 0, "negative": 0}
    for src in sources:
        # sentiment_engine returns bearish/bullish/neutral — map to the
        # story-facing vocabulary.
        key = {
            "bullish": "positive",
            "bearish": "negative",
            "neutral": "neutral",
        }.get(src.sentiment, "neutral")
        dist[key] = dist.get(key, 0) + 1
    return dist


def _bias_distribution(sources: list[StorySource]) -> dict[str, int]:
    """Count sources by editorial leaning (left/center/right/unknown)."""
    dist: dict[str, int] = {"left": 0, "center": 0, "right": 0, "unknown": 0}
    for src in sources:
        leaning = src.leaning if src.leaning in dist else "unknown"
        dist[leaning] += 1
    return dist


# Protocol names reused for topic extraction (keep in sync with news_rating).
_TOPIC_PROTOCOL_RE = re.compile(
    r"\b(uniswap|aave|compound|curve|lido|makerdao|eigenlayer|arbitrum|optimism|"
    r"polygon|solana|ethereum|bitcoin|tether|chainlink|pyth|wormhole|layerzero|"
    r"hyperliquid|tornado cash|euler|mango|platypus|radiant|venus|benqi|cream|"
    r"terra|luna|ftx|alameda|celsius|blockfi|voyager|genesis|ronin|axie|nomad|"
    r"wintermute|jupiter|raydium|metaplex|opensea|blur|balancer|sushiswap|"
    r"pancakeswap|dydx|synthetix|yearn|convex|rocket pool)\b",
    re.IGNORECASE,
)


def _extract_topics(articles: list[dict[str, Any]], sources: list[StorySource]) -> list[str]:
    """Derive story topics from article categories + protocols mentioned."""
    topics: set[str] = set()
    for article in articles:
        category = str(article.get("category") or "").strip().lower()
        if category and category != "crypto":
            topics.add(category)
        for t in article.get("topics") or []:
            if isinstance(t, str) and t.strip():
                topics.add(t.strip().lower())
    for src in sources:
        text = f"{src.title} {src.summary}"
        for m in _TOPIC_PROTOCOL_RE.finditer(text):
            topics.add(m.group(1).lower())
    # Deterministic order — largest to smallest, then alpha.
    return sorted(topics, key=lambda t: (-len(t), t))[:8]
