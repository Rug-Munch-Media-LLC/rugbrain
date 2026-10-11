"""RugBrain — real-time crypto security alert ingestion.

Polls free security-intel sources every ``ALERT_POLL_INTERVAL`` (default
300s) and, whenever a NEW incident is detected:

  1. embeds the incident description (llama.cpp Qwen3-Embedding-8B, 2560-dim)
  2. stores it in the Qdrant ``security_alerts`` collection
  3. pushes to NATS subject ``alerts.crypto.security`` for downstream
     consumers (telegram bot, alert pipelines, etc.) — or falls back to
     Redis pub/sub on the same subject name when NATS is not configured
  4. logs to ClickHouse ``rag_security_alerts`` for historical tracking

Sources (free tier, max one poll per source per interval):

  - CoinGecko recently-added / new token launches
  - DeFiLlama hacks API (https://api.llama.fi/hacks) — major exploits
  - Rekt News leaderboard — major exploits as they happen
  - Crypto security Twitter/X accounts (CertiK, PeckShield, Beosin,
    SpreekAway) via Nitter RSS, falling back to SearXNG (local, port 8089)

Rate limiting: each source keeps a ``last_poll`` timestamp in Redis
(``rag:alerts:last_poll:<source>``) and is only polled when the minimum
interval has elapsed. Individual sources can be disabled with
``ALERT_<SOURCE>_ENABLED=false`` (e.g. ``ALERT_TWITTER_ENABLED=false``).

Usage::

    python -m rugbrain.alerts --once        # single poll pass (cron friendly)
    python -m rugbrain.alerts               # long-running poller (every 60s tick)

Requires (all optional, degrade gracefully when absent):
  - Qdrant (QDRANT_URL) + Qwen3-8B (QWEN_EMBED_URL) for embedding/storage
  - Redis (REDIS_HOST) for rate-limit + dedup state
  - NATS (NATS_URL) — optional; Redis pub/sub is the fallback bus
  - ClickHouse (CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE) — optional;
    skipped with a warning when unreachable

Submodules (one responsibility each): ``config``, ``models``, ``state``,
``helpers``, ``embedding``, ``storage``, ``bus``, ``clickhouse``, ``dates``,
``vectors``, ``rss``, ``poller_coingecko``, ``poller_defillama``,
``poller_rekt``, ``poller_twitter``, ``poller_searxng``, ``runner``,
``poller``, ``loop``, ``cli``.

Tests patch module attributes on this package object (e.g. ``SOURCE_POLLERS``,
``AlertBus``, ``AlertState._client``); internal callers resolve those names
through this namespace at call time so patches apply.
"""

from __future__ import annotations

import logging

from .bus import AlertBus as AlertBus
from .cli import main as main
from .clickhouse import ClickHouseAlertLogger as ClickHouseAlertLogger
from .config import ALERT_COLLECTION as ALERT_COLLECTION
from .config import CLICKHOUSE_DATABASE as CLICKHOUSE_DATABASE
from .config import CLICKHOUSE_FALLBACK_DATABASE as CLICKHOUSE_FALLBACK_DATABASE
from .config import CLICKHOUSE_HOST as CLICKHOUSE_HOST
from .config import CLICKHOUSE_INTERFACE as CLICKHOUSE_INTERFACE
from .config import CLICKHOUSE_PASSWORD as CLICKHOUSE_PASSWORD
from .config import CLICKHOUSE_PORT as CLICKHOUSE_PORT
from .config import CLICKHOUSE_TABLE as CLICKHOUSE_TABLE
from .config import CLICKHOUSE_USER as CLICKHOUSE_USER
from .config import COINGECKO_MARKETS_URL as COINGECKO_MARKETS_URL
from .config import DEFILLAMA_HACKS_URL as DEFILLAMA_HACKS_URL
from .config import EMBEDDING_DIM as EMBEDDING_DIM
from .config import NATS_SUBJECT as NATS_SUBJECT
from .config import NATS_URL as NATS_URL
from .config import NITTER_URL as NITTER_URL
from .config import POLL_INTERVAL as POLL_INTERVAL
from .config import QDRANT_URL as QDRANT_URL
from .config import QWEN_EMBED_MODEL_NAME as QWEN_EMBED_MODEL_NAME
from .config import QWEN_EMBED_URL as QWEN_EMBED_URL
from .config import RECENT_WINDOW_DAYS as RECENT_WINDOW_DAYS
from .config import REDIS_HOST as REDIS_HOST
from .config import REDIS_PASSWORD as REDIS_PASSWORD
from .config import REDIS_PORT as REDIS_PORT
from .config import REKT_LEADERBOARD_URL as REKT_LEADERBOARD_URL
from .config import SEARXNG_URL as SEARXNG_URL
from .config import SECURITY_ACCOUNTS as SECURITY_ACCOUNTS
from .config import SEEN_TTL as SEEN_TTL
from .config import TICK_INTERVAL as TICK_INTERVAL
from .dates import _parse_date_iso as _parse_date_iso
from .dates import _ts_to_iso as _ts_to_iso
from .dates import _within_window as _within_window
from .embedding import _embed as _embed
from .helpers import _enabled as _enabled
from .helpers import _rate_limited as _rate_limited
from .loop import run_alert_poller as run_alert_poller
from .models import SecurityAlert as SecurityAlert
from .poller import poll_once as poll_once
from .poller_coingecko import poll_coingecko as poll_coingecko
from .poller_defillama import poll_defillama as poll_defillama
from .poller_rekt import poll_rekt as poll_rekt
from .poller_searxng import _searxng_fallback as _searxng_fallback
from .poller_twitter import poll_twitter as poll_twitter
from .rss import _parse_rss_items as _parse_rss_items
from .rss import _tag_text as _tag_text
from .runner import _run_source as _run_source
from .state import AlertState as AlertState
from .storage import _ensure_alert_collection as _ensure_alert_collection
from .storage import store_alert as store_alert
from .vectors import _CHAIN_ALIASES as _CHAIN_ALIASES
from .vectors import _VECTOR_PATTERNS as _VECTOR_PATTERNS
from .vectors import detect_chain as detect_chain
from .vectors import detect_vector as detect_vector

log = logging.getLogger("rugbrain.alerts")

# Source registry — patched by tests (``monkeypatch.setattr(mod, "SOURCE_POLLERS", ...)``)
# and resolved at call time by ``runner._run_source`` / ``poller.poll_once``.
SOURCE_POLLERS: dict[str, object] = {
    "coingecko": poll_coingecko,
    "defillama": poll_defillama,
    "rekt": poll_rekt,
    "twitter": poll_twitter,
}

__all__ = [
    "ALERT_COLLECTION",
    "CLICKHOUSE_DATABASE",
    "CLICKHOUSE_FALLBACK_DATABASE",
    "CLICKHOUSE_HOST",
    "CLICKHOUSE_INTERFACE",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_PORT",
    "CLICKHOUSE_TABLE",
    "CLICKHOUSE_USER",
    "COINGECKO_MARKETS_URL",
    "DEFILLAMA_HACKS_URL",
    "EMBEDDING_DIM",
    "NATS_SUBJECT",
    "NATS_URL",
    "NITTER_URL",
    "POLL_INTERVAL",
    "QDRANT_URL",
    "QWEN_EMBED_MODEL_NAME",
    "QWEN_EMBED_URL",
    "RECENT_WINDOW_DAYS",
    "REDIS_HOST",
    "REDIS_PASSWORD",
    "REDIS_PORT",
    "REKT_LEADERBOARD_URL",
    "SEARXNG_URL",
    "SECURITY_ACCOUNTS",
    "SEEN_TTL",
    "SOURCE_POLLERS",
    "TICK_INTERVAL",
    "AlertBus",
    "AlertState",
    "ClickHouseAlertLogger",
    "SecurityAlert",
    "detect_chain",
    "detect_vector",
    "main",
    "poll_coingecko",
    "poll_defillama",
    "poll_once",
    "poll_rekt",
    "poll_twitter",
    "run_alert_poller",
    "store_alert",
]
