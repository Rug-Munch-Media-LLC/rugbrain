"""Environment configuration for the realtime exploit-alert subsystem.

Values are read once at import (matching the original single-module
implementation). Thresholds that tests patch on the package
(``ETH_RPC_URL``, ``SOLANA_RPC_URL``, ``LARGE_TX_USD``, ``SOL_PRICE_USD``)
are re-exported by ``rugbrain.realtime_alerts`` and read through that
namespace at call time.
"""

from __future__ import annotations

import os

from rugbrain.dim_policy import default_dim_for_model

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8089").rstrip("/")
# Crypto security accounts tracked via SearXNG (X/Twitter search).
EXPLOIT_ACCOUNTS = tuple(
    a.strip()
    for a in os.getenv(
        "REALTIME_EXPLOIT_ACCOUNTS",
        "CertiKAlert,PeckshieldAlert,BeosinAlert,BlockSecAlert,SpreekAway,zachxbt",
    ).split(",")
    if a.strip()
)
TWITTER_POLL_SECONDS = float(os.getenv("REALTIME_TWITTER_POLL_SECONDS", "30"))
ONCHAIN_POLL_SECONDS = float(os.getenv("REALTIME_ONCHAIN_POLL_SECONDS", "60"))
RPC_POLL_SECONDS = float(os.getenv("REALTIME_RPC_POLL_SECONDS", "30"))
MONITOR_TICK = float(os.getenv("REALTIME_MONITOR_TICK", "10"))  # loop scheduler granularity

CRITICAL_NATS_SUBJECT = os.getenv("REALTIME_NATS_SUBJECT", "alerts.crypto.critical")

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "rmi")
# The fleet ``rmi`` ClickHouse user can only CREATE TABLE on wallet_memory.
CLICKHOUSE_FALLBACK_DATABASE = os.getenv("CLICKHOUSE_FALLBACK_DATABASE", "wallet_memory")
CRITICAL_ALERTS_TABLE = os.getenv("CLICKHOUSE_CRITICAL_TABLE", "critical_alerts")

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1").rstrip("/")
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
# Registry-aware default (native dim of the configured model); env still wins.
EMBEDDING_DIM = int(os.getenv("QWEN_EMBED_DIMS") or default_dim_for_model(QWEN_EMBED_MODEL_NAME))
LIVE_THREATS_COLLECTION = os.getenv("LIVE_THREATS_COLLECTION", "live_threats")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")

# Exploit classification / dedup.
SEEN_TTL = int(os.getenv("REALTIME_SEEN_TTL", str(60 * 60 * 24)))  # dedup window (24h)
LIVE_TTL_SECONDS = int(os.getenv("REALTIME_LIVE_TTL", str(60 * 60 * 24)))  # live threat shelf life
MAX_RECENT = int(os.getenv("REALTIME_MAX_RECENT", "200"))
LARGE_TX_USD = float(os.getenv("REALTIME_LARGE_TX_USD", "1000000"))
NEW_ADDRESS_MAX_TXS = int(os.getenv("REALTIME_NEW_ADDRESS_MAX_TXS", "5"))
ONCHAIN_LOOKBACK_MINUTES = int(os.getenv("REALTIME_ONCHAIN_LOOKBACK_MINUTES", "10"))
ORACLE_WHALE_MIN = int(os.getenv("REALTIME_ORACLE_WHALE_MIN", "3"))
ORACLE_VOLUME_USD = float(os.getenv("REALTIME_ORACLE_VOLUME_USD", "2000000"))

# RPC monitoring (disabled until an operator sets the endpoints — RugMunch
# policy: no hardcoded RPC URLs in code; supply via env on the host).
ETH_RPC_URL = os.getenv("ETH_RPC_URL", "").strip()
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "").strip()
# ETH/SOL prices: env values are only the offline fallback for the live price.
ETH_PRICE_USD = float(os.getenv("ETH_PRICE_USD", "0"))
SOL_PRICE_USD = float(os.getenv("SOL_PRICE_USD", "0"))
ETH_LARGE_TX_WEI = float(os.getenv("ETH_LARGE_TX_WEI", "0"))  # fallback when price unset
SOLANA_LARGE_TX_LAMPORTS = float(os.getenv("SOLANA_LARGE_TX_LAMPORTS", "0"))
RPC_MAX_TXS = int(os.getenv("REALTIME_RPC_MAX_TXS", "200"))

WEBHOOK_REDIS_PREFIX = "rag:webhooks:"
SEEN_REDIS_PREFIX = "rag:exploit:seen:"
RECENT_REDIS_KEY = "rag:recent_alerts"
