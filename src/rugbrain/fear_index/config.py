"""Configuration and shared constants for the crypto fear index package."""

from __future__ import annotations

import os

# ── Polling / source configuration ──────────────────────────────────────────
FEAR_INDEX_POLL_SECONDS = int(os.getenv("FEAR_INDEX_POLL_SECONDS", "300"))
FNG_URL = os.getenv("FEAR_GREED_URL", "https://api.alternative.me/fng/?limit=1")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8889/search")
SEARXNG_QUERY = os.getenv("FEAR_SOCIAL_QUERY", "bitcoin ethereum crypto market")
ONCHAIN_SIGNALS_URL = os.getenv("ONCHAIN_SIGNALS_URL", "")

# ── Redis cache configuration ───────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
CACHE_KEY = os.getenv("FEAR_INDEX_CACHE_KEY", "rag:fear_index:current")
CACHE_TTL = int(os.getenv("FEAR_INDEX_CACHE_TTL", str(FEAR_INDEX_POLL_SECONDS + 60)))

# ── ClickHouse persistence configuration ────────────────────────────────────
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "rmi")
CLICKHOUSE_FALLBACK_DATABASE = os.getenv("CLICKHOUSE_FALLBACK_DATABASE", "wallet_memory")
CLICKHOUSE_TABLE = os.getenv("CLICKHOUSE_FEAR_TABLE", "crypto_fear_index")

#: Component → composite weight. Missing components renormalize the rest.
COMPONENT_WEIGHTS: dict[str, float] = {
    "fng": 0.35,
    "social": 0.20,
    "onchain": 0.25,
    "news": 0.20,
}

_WINDOW_SECONDS: dict[str, int] = {
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
    "7d": 604800,
}

POSITIVE_TERMS: tuple[str, ...] = (
    "bull",
    "surge",
    "rally",
    "recovery",
    "breakout",
    "accumulation",
    "adoption",
    "all-time high",
    "optimism",
    "inflows",
    "outflows",
)
NEGATIVE_TERMS: tuple[str, ...] = (
    "crash",
    "plunge",
    "selloff",
    "fear",
    "panic",
    "bear",
    "liquidation",
    "bankrupt",
    "scam",
    "hack",
    "regulation",
    "ban",
    "dip",
    "capitulation",
)
