"""RugBrain — real-time exploit detection and community alerting.

Public package surface for the former ``rugbrain.realtime_alerts`` module.
Three fast monitors (SearXNG security accounts, ClickHouse on-chain
anomalies, ETH/Solana RPC large transactions) detect exploits and push
them through a 5-step pipeline (NATS/pub-sub, ClickHouse, Telegram,
Qdrant ``live_threats``, community webhooks) in under a minute.

Submodules (one responsibility each): ``config``, ``pricing``,
``heuristics``, ``models``, ``signing``, ``payloads``, ``redis_client``,
``webhooks``, ``store``, ``clickhouse_logger``, ``qdrant_store``,
``telegram``, ``poller_searxng``, ``poller_onchain``, ``poller_rpc``,
``monitor``, ``watchlist``, ``cli``.

Usage::

    python -m rugbrain.realtime_alerts            # long-running monitor
    python -m rugbrain.realtime_alerts --once     # single pass (cron)

Everything degrades gracefully when a dependency is missing or a service
is unreachable — an exploit is still recorded in-memory + Redis and the
API can serve it, even with every external system down.

Tests patch module attributes on the package object (e.g. ``_get_redis``,
``store_qdrant_live_threat``, ``resolve_native_price``); internal callers
resolve those names through this namespace at call time so patches apply.
"""

from __future__ import annotations

import logging

import httpx as httpx
from rugbrain.alerts import AlertBus as AlertBus

from .clickhouse_logger import CriticalAlertLogger
from .clickhouse_logger import _critical_logger as _critical_logger
from .cli import main as main
from .config import CRITICAL_ALERTS_TABLE as CRITICAL_ALERTS_TABLE
from .config import ETH_PRICE_USD as ETH_PRICE_USD
from .config import CRITICAL_NATS_SUBJECT as CRITICAL_NATS_SUBJECT
from .config import ETH_RPC_URL as ETH_RPC_URL
from .config import EXPLOIT_ACCOUNTS as EXPLOIT_ACCOUNTS
from .config import LARGE_TX_USD as LARGE_TX_USD
from .config import LIVE_TTL_SECONDS as LIVE_TTL_SECONDS
from .config import MAX_RECENT as MAX_RECENT
from .config import MONITOR_TICK as MONITOR_TICK
from .config import ONCHAIN_LOOKBACK_MINUTES as ONCHAIN_LOOKBACK_MINUTES
from .config import ONCHAIN_POLL_SECONDS as ONCHAIN_POLL_SECONDS
from .config import RPC_POLL_SECONDS as RPC_POLL_SECONDS
from .config import SEARXNG_URL as SEARXNG_URL
from .config import SEEN_TTL as SEEN_TTL
from .config import SOLANA_RPC_URL as SOLANA_RPC_URL
from .config import SOL_PRICE_USD as SOL_PRICE_USD
from .config import TWITTER_POLL_SECONDS as TWITTER_POLL_SECONDS
from .heuristics import detect_chain_critical, is_exploit_candidate, parse_amount_usd
from .models import CriticalAlert
from .monitor import (
    ExploitMonitor,
    _monitor as _monitor,
    is_realtime_monitor_running,
    realtime_monitor_stats,
    start_realtime_monitor,
    stop_realtime_monitor,
)
from .payloads import build_webhook_payload
from .poller_onchain import (
    _dex_whale_sql as _dex_whale_sql,
    _new_address_sql as _new_address_sql,
    _oracle_pattern_sql as _oracle_pattern_sql,
    poll_onchain_anomalies,
)
from .poller_rpc import (
    poll_ethereum_rpc as poll_ethereum_rpc,
    poll_rpc_large_txs,
    poll_solana_rpc as poll_solana_rpc,
)
from .poller_searxng import poll_searxng_exploits
from .pricing import (
    _fetch_live_price as _fetch_live_price,
    _price_cache as _price_cache,
    resolve_native_price as resolve_native_price,
)
from .qdrant_store import store_qdrant_live_threat
from .qdrant_store import scroll_live_threats as scroll_live_threats
from .redis_client import _get_redis as _get_redis
from .signing import sign_payload, verify_signature
from .store import (
    LiveThreatStore,
    _store as _store,
    get_live_threats,
    get_recent_alerts,
)
from .telegram import _send_telegram as _send_telegram
from .watchlist import AlertDispatcher, WatchlistManager
from .watchlist import start_alert_listener as start_alert_listener
from .webhooks import (
    WebhookRegistry,
    _webhook_registry as _webhook_registry,
    deliver_webhooks,
    get_webhook_registry,
)

log = logging.getLogger("rugbrain.realtime_alerts")

__all__ = [
    "AlertDispatcher",
    "CriticalAlert",
    "CriticalAlertLogger",
    "ExploitMonitor",
    "LiveThreatStore",
    "WatchlistManager",
    "WebhookRegistry",
    "build_webhook_payload",
    "deliver_webhooks",
    "detect_chain_critical",
    "get_live_threats",
    "get_recent_alerts",
    "get_webhook_registry",
    "is_exploit_candidate",
    "is_realtime_monitor_running",
    "parse_amount_usd",
    "poll_onchain_anomalies",
    "poll_rpc_large_txs",
    "poll_searxng_exploits",
    "realtime_monitor_stats",
    "sign_payload",
    "start_realtime_monitor",
    "stop_realtime_monitor",
    "store_qdrant_live_threat",
    "verify_signature",
]
