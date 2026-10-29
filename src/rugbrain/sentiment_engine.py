"""Crypto market sentiment engine — text analysis, token aggregation, market mood.

Provides lightweight sentiment scoring without external NLP APIs. Uses
crypto-specific keyword detection, pattern matching, and signal scoring
to produce structured sentiment outputs for tokens and the broader market.

  * ``analyze_text_sentiment(text)``      → {sentiment, confidence, signals}
  * ``analyze_token_sentiment(address)``  → aggregate from social/news/on-chain
  * ``analyze_market_sentiment()``        → overall mood from N sources

All functions are synchronous (no IO), returning results immediately. The
token-level and market-level functions delegate to the RAG collection for
context but degrade gracefully when backends are unavailable.

Usage::

    from rugbrain.sentiment_engine import analyze_text_sentiment

    result = analyze_text_sentiment("This token is about to rug pull hard")
    # {"sentiment": "bearish", "confidence": 0.82, "signals": ["rug_pull", ...]}

API Endpoints (mounted at /api/v1/sentiment/*)::

    POST /api/v1/sentiment/analyze       — score raw text
    GET  /api/v1/sentiment/token/{addr}  — aggregated token sentiment
    GET  /api/v1/sentiment/market        — overall market mood
"""

from __future__ import annotations

import logging

from rugbrain.sentiment_engine_pkg.aggregate import (
    analyze_market_sentiment as analyze_market_sentiment,
)
from rugbrain.sentiment_engine_pkg.aggregate import (
    analyze_token_sentiment as analyze_token_sentiment,
)
from rugbrain.sentiment_engine_pkg.lexicon import (
    _BEARISH_KEYWORDS as _BEARISH_KEYWORDS,
)
from rugbrain.sentiment_engine_pkg.lexicon import (
    _BULLISH_KEYWORDS as _BULLISH_KEYWORDS,
)
from rugbrain.sentiment_engine_pkg.lexicon import (
    _NEUTRAL_KEYWORDS as _NEUTRAL_KEYWORDS,
)
from rugbrain.sentiment_engine_pkg.models import (
    MarketSentiment as MarketSentiment,
)
from rugbrain.sentiment_engine_pkg.models import (
    SentimentResult as SentimentResult,
)
from rugbrain.sentiment_engine_pkg.models import (
    TokenSentiment as TokenSentiment,
)
from rugbrain.sentiment_engine_pkg.routes import (
    sentiment_router as sentiment_router,
)
from rugbrain.sentiment_engine_pkg.scoring import (
    _apply_modifiers as _apply_modifiers,
)
from rugbrain.sentiment_engine_pkg.scoring import (
    _extract_keywords as _extract_keywords,
)
from rugbrain.sentiment_engine_pkg.scoring import (
    analyze_text_sentiment as analyze_text_sentiment,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MarketSentiment",
    "SentimentResult",
    "TokenSentiment",
    "analyze_market_sentiment",
    "analyze_text_sentiment",
    "analyze_token_sentiment",
    "sentiment_router",
]
