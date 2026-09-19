"""Configuration constants for the LunarCrush social sentiment monitor."""

from __future__ import annotations

import os

# LunarCrush API v2 (free tier)
API_KEY: str = os.getenv("LUNARCRUSH_API_KEY", "")
API_BASE: str = "https://lunarcrush.com/api/v2"

# Background loop interval (5 minutes default)
POLL_INTERVAL: int = int(os.getenv("LUNARCRUSH_POLL_INTERVAL", "300"))

# Rate limiter: 1 req/sec for free tier
MIN_REQUEST_GAP: float = float(os.getenv("LUNARCRUSH_RATE_LIMIT_SEC", "1.0"))

# How many historical data points to keep per symbol for 24h change tracking
HISTORY_WINDOW: int = int(os.getenv("LUNARCRUSH_HISTORY_WINDOW", "288"))  # 288 * 5min = 24h

# Alert thresholds
SENTIMENT_BULLISH_THRESHOLD: int = int(os.getenv("LC_SENTIMENT_BULLISH", "80"))
SENTIMENT_BEARISH_THRESHOLD: int = int(os.getenv("LC_SENTIMENT_BEARISH", "20"))
VOLUME_SPIKE_THRESHOLD: float = float(os.getenv("LC_VOLUME_SPIKE", "3.0"))  # 3x normal
VOLUME_SPIKE_MILD_THRESHOLD: float = float(os.getenv("LC_VOLUME_SPIKE_MILD", "2.0"))  # 2x
SENTIMENT_REVERSAL_THRESHOLD: int = int(os.getenv("LC_SENTIMENT_REVERSAL", "30"))  # 30+ pts

# Max items per API call
DEFAULT_LIMIT: int = int(os.getenv("LUNARCRUSH_DEFAULT_LIMIT", "50"))
