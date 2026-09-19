"""In-memory shared state for the LunarCrush social sentiment monitor.

All mutable stores live here and are accessed by sibling modules through the
package namespace (``rugbrain.monitors.social_sentiment``), so monkeypatching
a package attribute at call time stays effective.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

from .config import HISTORY_WINDOW

# ── Rate limiter state ──────────────────────────────────────────────────────
_last_request_time: float = 0.0
_rate_limit_lock = asyncio.Lock()

# ── In-memory stores ────────────────────────────────────────────────────────
# Per-coin social stats: {symbol: {...}}
_coin_stats: dict[str, dict[str, Any]] = {}

# Historical sentiment data for 24h rolling window: {symbol: deque of {...}}
_sentiment_history: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=HISTORY_WINDOW)
)

# Historical social volume for spike detection: {symbol: deque of {...}}
_volume_history: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=HISTORY_WINDOW)
)

# Trending coins list (latest snapshot)
_trending_coins: list[dict[str, Any]] = []

# Social feed posts
_social_feed: list[dict[str, Any]] = []

# Alerts generated in the current cycle
_recent_alerts: deque[dict[str, Any]] = deque(maxlen=100)

# Health state
_health_state: dict[str, Any] = {
    "last_successful_poll": 0.0,
    "last_error": "",
    "consecutive_failures": 0,
    "total_polls": 0,
    "coins_tracked": 0,
    "status": "initializing",
}

# Seen post IDs for dedup
_seen_posts: set[str] = set()
