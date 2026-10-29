"""Sentiment engine configuration (env read at import, as before)."""

from __future__ import annotations

import os

SENTIMENT_COLLECTION = os.getenv("RAG_SENTIMENT_COLLECTION", "market_sentiment")
