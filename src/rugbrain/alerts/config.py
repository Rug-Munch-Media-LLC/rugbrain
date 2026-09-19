"""Configuration for the security-alert ingestion package.

Every value is env-overridable and degrades gracefully when the backing
service is absent (Qdrant, Redis, NATS, ClickHouse are all optional).
"""

from __future__ import annotations

import os

from rugbrain.dim_policy import default_dim_for_model

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1").rstrip("/")
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
# Registry-aware default (native dim of the configured model); env still wins.
# 2026-08-25: was hardcoded 1024 while :8004 serves native 2560 — full-fidelity
# vectors now; live_threats/alert collections were empty so no migration needed.
EMBEDDING_DIM = int(os.getenv("QWEN_EMBED_DIMS") or default_dim_for_model(QWEN_EMBED_MODEL_NAME))

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

NATS_URL = os.getenv("NATS_URL", "")  # e.g. nats://127.0.0.1:4222 — empty disables NATS
NATS_SUBJECT = os.getenv("ALERTS_NATS_SUBJECT", "alerts.crypto.security")

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "rmi")
# The fleet's ``rmi`` ClickHouse user can CREATE TABLE only on
# ``wallet_memory.*`` (which already holds scam_addresses), so the alert
# ledger falls back there when the primary DB is read-only for the user.
CLICKHOUSE_FALLBACK_DATABASE = os.getenv("CLICKHOUSE_FALLBACK_DATABASE", "wallet_memory")
CLICKHOUSE_TABLE = os.getenv("CLICKHOUSE_ALERTS_TABLE", "rag_security_alerts")

ALERT_COLLECTION = os.getenv("ALERT_COLLECTION", "security_alerts")
POLL_INTERVAL = float(os.getenv("ALERT_POLL_INTERVAL", "300"))  # seconds per source
TICK_INTERVAL = float(os.getenv("ALERT_TICK_INTERVAL", "60"))  # loop sleep between passes
SEEN_TTL = int(os.getenv("ALERT_SEEN_TTL", str(60 * 60 * 24 * 30)))  # dedup window (30d)

NITTER_URL = os.getenv("NITTER_URL", "https://nitter.net").rstrip("/")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8089").rstrip("/")

# Crypto security accounts on X (aggregated via Nitter RSS → SearXNG fallback).
SECURITY_ACCOUNTS = tuple(
    os.getenv(
        "ALERT_SECURITY_ACCOUNTS", "certikalert,PeckshieldAlert,BeosinAlert,Spreekaway"
    ).split(",")
)

# Only incidents with a reported date within this window are treated as "new".
RECENT_WINDOW_DAYS = float(os.getenv("ALERT_RECENT_WINDOW_DAYS", "7"))

COINGECKO_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=gecko_desc&per_page=50&sparkline=false"
)
DEFILLAMA_HACKS_URL = "https://api.llama.fi/hacks"
REKT_LEADERBOARD_URL = "https://rekt.news/leaderboard"
