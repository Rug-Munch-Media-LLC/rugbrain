"""``/api/v1/sentiment/*`` endpoints (router + handlers).

Moved verbatim from ``sentiment_engine.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from rugbrain.sentiment_engine_pkg.aggregate import (
    analyze_market_sentiment,
    analyze_token_sentiment,
)
from rugbrain.sentiment_engine_pkg.scoring import analyze_text_sentiment

logger = logging.getLogger(__name__)

sentiment_router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment"])


# ── API Endpoints ────────────────────────────────────────────────────────────


@sentiment_router.post("/analyze")
async def sentiment_analyze_endpoint(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    """Analyze the sentiment of raw text (keyword-based, no external APIs).

    Body::

        {"text": "This token is about to rug pull hard, scam alert!"}

    Returns a ``SentimentResult`` as a dict with ``sentiment``, ``confidence``,
    ``signals`` (matched crypto keywords), and raw category ``scores``.
    """
    text = str(data.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail={"error": "text is required"})

    result = analyze_text_sentiment(text)
    return {
        "sentiment": result.sentiment,
        "confidence": result.confidence,
        "signals": result.signals,
        "scores": result.scores,
        "text_length": result.text_length,
        "analyzed_at": result.analyzed_at,
    }


@sentiment_router.get("/token/{address}")
async def sentiment_token_endpoint(
    request: Request,
    address: str,
    chain: str = Query(default="ethereum"),
    include_social: bool = Query(default=True),
    include_news: bool = Query(default=True),
) -> dict[str, Any]:
    """Aggregate sentiment for a token from RAG collections.

    Queries the ``threat_intel_v2``, ``news_articles``, and ``social_intel``
    Qdrant collections for content mentioning *address*, scores each piece,
    and returns an aggregated result::

        curl "http://localhost:8012/api/v1/sentiment/token/0xabc...?chain=ethereum"
    """
    result = await analyze_token_sentiment(
        address=address,
        chain=chain,
        include_social=include_social,
        include_news=include_news,
    )
    return {
        "address": result.address,
        "chain": result.chain,
        "sentiment": result.sentiment,
        "confidence": result.confidence,
        "signals": result.signals,
        "source_counts": result.source_counts,
        "analyzed_at": result.analyzed_at,
    }


@sentiment_router.get("/market")
async def sentiment_market_endpoint(
    request: Request,
    max_docs: int = Query(default=30, ge=10, le=200),
) -> dict[str, Any]:
    """Overall crypto market sentiment snapshot (-1=fear, +1=greed).

    Samples recent content from ``news_articles`` and ``social_intel``
    collections and returns an aggregate with a -1.0 to +1.0 score::

        curl "http://localhost:8012/api/v1/sentiment/market"
    """
    result = await analyze_market_sentiment(max_docs=max_docs)
    return {
        "sentiment": result.sentiment,
        "confidence": result.confidence,
        "score": result.score,
        "sources": result.sources,
        "top_signals": result.top_signals,
        "components": result.components,
        "analyzed_at": result.analyzed_at,
    }
