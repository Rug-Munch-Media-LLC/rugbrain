"""News-sentiment component for the crypto fear index (Qdrant ``news``)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("rugbrain.fear_index")


def _normalize_sentiment(value: Any) -> float | None:
    """Map a sentiment value to -1..1 (None when unparseable)."""
    from rugbrain import fear_index as _pkg

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("positive", "bullish", "greedy"):
            return 1.0
        if lowered in ("negative", "bearish", "fearful"):
            return -1.0
        if lowered in ("neutral", ""):
            return 0.0
        try:
            value = float(lowered)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        if -1.0 <= value <= 1.0:
            return float(value)
        if 0 <= value <= 100:
            return float(value) / 100.0 * 2.0 - 1.0
        return _pkg._clamp(value / 100.0, low=-100, high=100) / 100.0
    return None


def aggregate_news_sentiment(sentiments: list[float]) -> int | None:
    """Average -1..1 sentiment values into a 0-100 greed score."""
    from rugbrain import fear_index as _pkg

    if not sentiments:
        return None
    average = sum(sentiments) / len(sentiments)
    return _pkg._clamp((average + 1.0) / 2.0 * 100.0)


async def fetch_news_sentiment(_client: httpx.AsyncClient | None = None) -> int | None:
    """Aggregate sentiment of recently ingested news from Qdrant `news`.

    Reads the ``sentiment`` / ``sentiment_score`` metadata of recent points
    (written by ``news_to_rag.py``). Returns None when Qdrant is
    unreachable or no rated points exist.
    """
    from rugbrain import fear_index as _pkg

    try:
        from rugbrain.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        results, _ = await store.scroll("news_v2", limit=200)
        sentiments: list[float] = []
        for result in results:
            metadata = result.get("metadata") or {}
            normalized = _normalize_sentiment(
                metadata.get("sentiment", metadata.get("sentiment_score"))
            )
            if normalized is not None:
                sentiments.append(normalized)
        return _pkg.aggregate_news_sentiment(sentiments)
    except Exception as e:
        log.warning("fear_news_sentiment_error: %s", e)
        return None
