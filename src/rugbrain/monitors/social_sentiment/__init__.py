"""LunarCrush Social Sentiment Engine — comprehensive crypto social intelligence.

Tracks in real-time:
1. SOCIAL STATS: Per-coin social volume, sentiment, tweets, interactions
2. TRENDING COINS: Top coins by social activity with sentiment analysis
3. SENTIMENT TRENDS: 24h changes, volume spikes, sentiment reversals
4. SOCIAL FEED: Trending posts and discussions across crypto X/Twitter
5. ALERTS: Sentiment extremes, volume spikes, rapid reversals

Endpoints (LunarCrush API v2, free tier):
  GET https://lunarcrush.com/api/v2/public/coins/trending/v2?limit=N&key=KEY
    -> Top coins by social activity with sentiment scores
  GET https://lunarcrush.com/api/v2/public/coin/{symbol}/v2?key=KEY
    -> Specific coin social stats
  GET https://lunarcrush.com/api/v2/public/feed?key=KEY&limit=N
    -> Social feed / trending posts

Rate limits (free tier):
  - 1 request per second
  - Cached in-memory to avoid redundant calls
  - 5-minute background loop polling interval

Outputs stored in-memory for:
  - Dashboard API (via _store dict)
  - Alert dispatch via rugbrain.alert_dispatcher

This package is a behavior-identical split of the former
``social_sentiment.py`` module (906 lines). Sibling modules resolve shared
names through this package namespace at call time so that monkeypatching
``rugbrain.monitors.social_sentiment.<name>`` keeps working.
"""

from __future__ import annotations

import logging

from .alert_dispatch import _dispatch_alert as _dispatch_alert
from .alert_generation import _generate_alerts as _generate_alerts
from .api_client import _api_get as _api_get
from .change_24h import calculate_24h_change as calculate_24h_change
from .classification import classify_sentiment as classify_sentiment
from .classification import get_sentiment_label as get_sentiment_label
from .config import API_BASE as API_BASE
from .config import API_KEY as API_KEY
from .config import DEFAULT_LIMIT as DEFAULT_LIMIT
from .config import HISTORY_WINDOW as HISTORY_WINDOW
from .config import MIN_REQUEST_GAP as MIN_REQUEST_GAP
from .config import POLL_INTERVAL as POLL_INTERVAL
from .config import SENTIMENT_BEARISH_THRESHOLD as SENTIMENT_BEARISH_THRESHOLD
from .config import SENTIMENT_BULLISH_THRESHOLD as SENTIMENT_BULLISH_THRESHOLD
from .config import SENTIMENT_REVERSAL_THRESHOLD as SENTIMENT_REVERSAL_THRESHOLD
from .config import VOLUME_SPIKE_MILD_THRESHOLD as VOLUME_SPIKE_MILD_THRESHOLD
from .config import VOLUME_SPIKE_THRESHOLD as VOLUME_SPIKE_THRESHOLD
from .cycle import social_sentiment_full_cycle as social_sentiment_full_cycle
from .dashboard import get_social_sentiment_state as get_social_sentiment_state
from .fetch_coin import fetch_coin_social as fetch_coin_social
from .fetch_feed import fetch_social_feed as fetch_social_feed
from .fetch_trending import fetch_trending_coins as fetch_trending_coins
from .health import social_sentiment_health as social_sentiment_health
from .loop import social_sentiment_loop as social_sentiment_loop
from .parse import _parse_sentiment_score as _parse_sentiment_score
from .rate_limit import _throttle as _throttle
from .sentiment_reversal import detect_sentiment_reversal as detect_sentiment_reversal
from .state import _coin_stats as _coin_stats
from .state import _health_state as _health_state
from .state import _last_request_time as _last_request_time
from .state import _rate_limit_lock as _rate_limit_lock
from .state import _recent_alerts as _recent_alerts
from .state import _seen_posts as _seen_posts
from .state import _sentiment_history as _sentiment_history
from .state import _social_feed as _social_feed
from .state import _trending_coins as _trending_coins
from .state import _volume_history as _volume_history
from .volume_spike import detect_social_volume_spike as detect_social_volume_spike

logger = logging.getLogger(__name__)

__all__ = [
    # Configuration
    "API_BASE",
    "API_KEY",
    "DEFAULT_LIMIT",
    "HISTORY_WINDOW",
    "MIN_REQUEST_GAP",
    "POLL_INTERVAL",
    "SENTIMENT_BEARISH_THRESHOLD",
    "SENTIMENT_BULLISH_THRESHOLD",
    "SENTIMENT_REVERSAL_THRESHOLD",
    "VOLUME_SPIKE_MILD_THRESHOLD",
    "VOLUME_SPIKE_THRESHOLD",
    # API
    "calculate_24h_change",
    "classify_sentiment",
    "detect_sentiment_reversal",
    "detect_social_volume_spike",
    "fetch_coin_social",
    "fetch_social_feed",
    "fetch_trending_coins",
    "get_sentiment_label",
    "get_social_sentiment_state",
    "social_sentiment_full_cycle",
    "social_sentiment_health",
    "social_sentiment_loop",
]
