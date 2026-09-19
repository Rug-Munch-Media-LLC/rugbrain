"""RugBrain — crypto panic / fear index.

Polls several market-sentiment signals and blends them into a single
0-100 index (0 = extreme fear, 100 = extreme greed) that feeds
rugmunch.io's news ranking and token-risk scoring:

  * ``fng``      — Alternative.me Fear & Greed index (free API)
  * ``social``   — crypto social sentiment via SearXNG (self-hosted, no key)
  * ``onchain``  — exchange net flows / whale movements / liquidations
  * ``news``     — aggregate sentiment of recently ingested news (Qdrant)

Snapshots are computed every ``FEAR_INDEX_POLL_SECONDS`` (default 300s) by an
asyncio poller, persisted to ClickHouse ``crypto_fear_index`` (best-effort)
and cached in Redis. Every fetch fails OPEN: a dead provider is omitted and
the index is renormalized over the remaining components, never raising into
the API layer.

This package is a behavior-identical split of the former ``fear_index.py``
module (773 lines). Sibling modules resolve shared names through this
package namespace at call time so that monkeypatching
``rugbrain.fear_index.<name>`` keeps working.
"""

from __future__ import annotations

import logging

from .api import current_fear_value as current_fear_value
from .api import get_fear_index as get_fear_index
from .cache import _cache_get as _cache_get
from .cache import _cache_set as _cache_set
from .classify import classify_fear as classify_fear
from .composite import compute_fear_index as compute_fear_index
from .composite import fear_snapshot as fear_snapshot
from .config import _WINDOW_SECONDS as _WINDOW_SECONDS
from .config import CACHE_KEY as CACHE_KEY
from .config import CACHE_TTL as CACHE_TTL
from .config import CLICKHOUSE_DATABASE as CLICKHOUSE_DATABASE
from .config import CLICKHOUSE_FALLBACK_DATABASE as CLICKHOUSE_FALLBACK_DATABASE
from .config import CLICKHOUSE_HOST as CLICKHOUSE_HOST
from .config import CLICKHOUSE_INTERFACE as CLICKHOUSE_INTERFACE
from .config import CLICKHOUSE_PASSWORD as CLICKHOUSE_PASSWORD
from .config import CLICKHOUSE_PORT as CLICKHOUSE_PORT
from .config import CLICKHOUSE_TABLE as CLICKHOUSE_TABLE
from .config import CLICKHOUSE_USER as CLICKHOUSE_USER
from .config import COMPONENT_WEIGHTS as COMPONENT_WEIGHTS
from .config import FEAR_INDEX_POLL_SECONDS as FEAR_INDEX_POLL_SECONDS
from .config import FNG_URL as FNG_URL
from .config import NEGATIVE_TERMS as NEGATIVE_TERMS
from .config import ONCHAIN_SIGNALS_URL as ONCHAIN_SIGNALS_URL
from .config import POSITIVE_TERMS as POSITIVE_TERMS
from .config import REDIS_HOST as REDIS_HOST
from .config import REDIS_PASSWORD as REDIS_PASSWORD
from .config import REDIS_PORT as REDIS_PORT
from .config import SEARXNG_QUERY as SEARXNG_QUERY
from .config import SEARXNG_URL as SEARXNG_URL
from .history import _record as _record
from .history import _samples_for_window as _samples_for_window
from .history import _window_seconds as _window_seconds
from .history import fear_index_history as fear_index_history
from .news import _normalize_sentiment as _normalize_sentiment
from .news import aggregate_news_sentiment as aggregate_news_sentiment
from .news import fetch_news_sentiment as fetch_news_sentiment
from .normalize import _as_int_or_none as _as_int_or_none
from .normalize import _clamp as _clamp
from .normalize import _parse_dt as _parse_dt
from .normalize import _term_hit as _term_hit
from .poller import is_fear_index_poller_running as is_fear_index_poller_running
from .poller import poll_fear_index as poll_fear_index
from .poller import start_fear_index_poller as start_fear_index_poller
from .poller import stop_fear_index_poller as stop_fear_index_poller
from .risk import adjust_risk_for_market as adjust_risk_for_market
from .sources import fetch_fng as fetch_fng
from .sources import fetch_onchain_signals as fetch_onchain_signals
from .sources import fetch_social_sentiment as fetch_social_sentiment
from .state import _history as _history
from .state import _last_snapshot as _last_snapshot
from .state import _poller_lock as _poller_lock
from .state import _poller_task as _poller_task
from .state import _storage as _storage
from .storage import FearIndexStorage as FearIndexStorage

log = logging.getLogger("rugbrain.fear_index")

__all__ = [
    "COMPONENT_WEIGHTS",
    "FearIndexStorage",
    "adjust_risk_for_market",
    "aggregate_news_sentiment",
    "classify_fear",
    "compute_fear_index",
    "current_fear_value",
    "fear_index_history",
    "fear_snapshot",
    "fetch_fng",
    "fetch_news_sentiment",
    "fetch_onchain_signals",
    "fetch_social_sentiment",
    "get_fear_index",
    "is_fear_index_poller_running",
    "poll_fear_index",
    "start_fear_index_poller",
    "stop_fear_index_poller",
]
