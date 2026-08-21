"""RugBrain — real-time exploit detection and community alerting.

The 5-minute security-alert poller (``rugbrain.alerts``) is too slow for
major hacks. This module runs three fast monitors against low-latency
free sources and, when an exploit is detected, pushes it through a
4-step pipeline in under a minute:

  Detection sources (all optional, all fail-open):
    * SearXNG RSS  — @CertiKAlert, @PeckShieldAlert, @BeosinAlert,
      @BlockSecAlert, @SpreekAway, @zachxbt via the self-hosted SearXNG
      instance (``SEARXNG_URL``, default http://127.0.0.1:8089).
      Polled every ``REALTIME_TWITTER_POLL_SECONDS`` (default 30s).
    * ClickHouse   — on-chain anomalies from the LiteChainIndexer hot
      store: whale DEX swaps, large transfers to fresh addresses, and
      rapid whale-activity (oracle-manipulation proxy) patterns. Polled
      every ``REALTIME_ONCHAIN_POLL_SECONDS`` (default 60s).
    * RPC          — near-real-time large transactions on Ethereum and
      Solana free RPCs (``ETH_RPC_URL`` / ``SOLANA_RPC_URL``; disabled
      when unset). Polled every ``REALTIME_RPC_POLL_SECONDS`` (default 30s).

  On exploit detection:
    1. publish to NATS ``alerts.crypto.critical`` (Redis pub/sub fallback)
    2. store in ClickHouse ``critical_alerts``
    3. push to Telegram (``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_CHAT_ID``)
    4. upsert into Qdrant ``live_threats`` (embedded via Qwen3)
    5. POST to every registered community webhook (HMAC-SHA256 signed)

The existing watchlist classes (``WatchlistManager`` / ``AlertDispatcher``)
are kept for backward compatibility with the ``rugbrain`` package surface.

Usage::

    python -m rugbrain.realtime_alerts            # long-running monitor
    python -m rugbrain.realtime_alerts --once     # single pass (cron)

Everything degrades gracefully when a dependency is missing or a service
is unreachable — an exploit is still recorded in-memory + Redis and the
API can serve it, even with every external system down.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import importlib
import json
import logging
import os
import re
import time
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field

from rugbrain.alerts import AlertBus

log = logging.getLogger("rugbrain.realtime_alerts")

# ── Configuration ─────────────────────────────────────────────────────────

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
INFINITY_URL = os.getenv("INFINITY_URL", "http://172.22.0.1:8004/v1").rstrip("/")
INFINITY_MODEL = os.getenv("RAG_INFINITY_EMBED_MODEL", "qwen3-embedding-4b")
EMBEDDING_DIM = int(os.getenv("QWEN_EMBED_DIMS", os.getenv("INFINITY_EMBED_DIMS", "2560")))
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
ETH_PRICE_USD = float(os.getenv("ETH_PRICE_USD", "0"))
SOL_PRICE_USD = float(os.getenv("SOL_PRICE_USD", "0"))
ETH_LARGE_TX_WEI = float(os.getenv("ETH_LARGE_TX_WEI", "0"))  # fallback when price unset
SOLANA_LARGE_TX_LAMPORTS = float(os.getenv("SOLANA_LARGE_TX_LAMPORTS", "0"))
RPC_MAX_TXS = int(os.getenv("REALTIME_RPC_MAX_TXS", "200"))

WEBHOOK_REDIS_PREFIX = "rag:webhooks:"
SEEN_REDIS_PREFIX = "rag:exploit:seen:"
RECENT_REDIS_KEY = "rag:recent_alerts"

# ── Exploit heuristics ────────────────────────────────────────────────────

_EXPLOIT_RE = re.compile(
    r"exploit|hack|drain|stolen|stole|steal|compromis|rug\b|vulnerab|attack|"
    r"breach|phish|key leak|private key|approved|swept|drained",
    re.I,
)
_AMOUNT_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s?([mMkKbB])?", re.I)
_AMOUNT_MULT = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9, "K": 1e3, "M": 1e6, "B": 1e9}
_CHAIN_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("ethereum", ("ethereum", "eth", "erc-20", "erc-721", "mainnet")),
    ("bsc", ("bsc", "binance", "bnb chain", "bep-20")),
    ("solana", ("solana", "spl")),
    ("polygon", ("polygon", "matic")),
    ("arbitrum", ("arbitrum", "arb")),
    ("optimism", ("optimism", "op mainnet")),
    ("base", ("base",)),
    ("avalanche", ("avalanche", "avax")),
    ("tron", ("tron", "trc-20")),
    ("fantom", ("fantom", "ftm")),
    ("multi", ("multi-chain", "cross-chain", "multiple chains")),
]

_CRITICAL_KEYWORDS = (
    "exploit",
    "drain",
    "stolen",
    "compromis",
    "critical",
    "urgent",
    "vulnerab",
    "attack",
)


def is_exploit_candidate(text: str) -> bool:
    """Heuristic: does *text* look like an exploit/hack report?"""
    return bool(_EXPLOIT_RE.search(text or ""))


def parse_amount_usd(text: str) -> float:
    """Best-effort ``$1.2M`` / ``$4,800,000`` / ``$300k`` extraction."""
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return 0.0
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0
    mult = _AMOUNT_MULT.get(m.group(2) or "", 1.0)
    return value * mult


def detect_chain_critical(text: str) -> str:
    lowered = (text or "").lower()
    for chain, aliases in _CHAIN_ALIASES:
        for alias in aliases:
            if alias in lowered:
                return chain
    return "unknown"


# ── Model ─────────────────────────────────────────────────────────────────


class CriticalAlert(BaseModel):
    """A single detected exploit/critical threat.

    ``id`` is a deterministic UUIDv5 derived from ``source + title +
    detected_at`` so re-detection of the same incident maps to the same
    store point (upserts are idempotent).
    """

    id: str = ""
    title: str
    severity: str = "critical"
    chain: str = "unknown"
    source: str = "unknown"
    description: str = ""
    amount_usd: float = 0.0
    addresses: list[str] = Field(default_factory=list)
    tx_hash: str = ""
    url: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    detected_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: str = "active"

    @classmethod
    def from_candidate(cls, **kwargs: Any) -> CriticalAlert:
        """Build an alert, filling classification fields from free text."""
        text = " ".join(str(v) for v in (kwargs.get("title", ""), kwargs.get("description", "")))
        kwargs.setdefault("severity", "critical")
        kwargs.setdefault("chain", detect_chain_critical(text))
        kwargs.setdefault("amount_usd", parse_amount_usd(text))
        return cls(**kwargs)

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            basis = f"{self.source}:{self.title}:{self.detected_at}"
            self.id = str(uuid.uuid5(uuid.NAMESPACE_URL, basis))

    def text(self) -> str:
        parts = [self.title, self.description]
        if self.amount_usd:
            parts.append(f"${self.amount_usd:,.0f} lost")
        return " | ".join(p for p in parts if p)


# ── HMAC helpers (community webhook trust) ───────────────────────────────


def sign_payload(secret: str, payload: bytes | str) -> str:
    """Return ``sha256=<hex>`` HMAC-SHA256 signature of *payload*."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    digest = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, signature: str, payload: bytes | str) -> bool:
    """Constant-time check that *signature* matches the HMAC of *payload*."""
    expected = sign_payload(secret, payload)
    return hmac.compare_digest(expected, signature or "")


def build_webhook_payload(alert: CriticalAlert) -> dict[str, Any]:
    """The JSON body community webhooks receive for a critical alert."""
    return {
        "event": "critical_alert",
        "id": alert.id,
        "title": alert.title,
        "severity": alert.severity,
        "chain": alert.chain,
        "source": alert.source,
        "amount_usd": alert.amount_usd,
        "description": alert.description,
        "addresses": alert.addresses,
        "tx_hash": alert.tx_hash,
        "url": alert.url,
        "detected_at": alert.detected_at,
    }


# ── Redis helpers ─────────────────────────────────────────────────────────


async def _get_redis() -> Any | None:
    """Lazy async Redis client (None when unavailable)."""
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        await r.ping()
        return r
    except Exception as e:
        log.debug("redis_unavailable: %s", e)
        return None


# ── Webhook registry ──────────────────────────────────────────────────────


class WebhookRegistry:
    """Community webhook subscriptions (Redis-backed, in-memory fallback).

    Every registration gets a generated ``webhook_id`` and an HMAC secret
    (returned exactly once). Deliveries are signed so receivers can verify
    the payload really came from RugBrain.
    """

    def __init__(self) -> None:
        self._memory: dict[str, dict[str, Any]] = {}
        self._redis: Any = None

    async def _client(self) -> Any | None:
        if self._redis is None:
            self._redis = await _get_redis()
        return self._redis

    async def register(
        self,
        url: str,
        name: str = "",
        secret: str = "",
    ) -> dict[str, Any]:
        webhook_id = uuid.uuid4().hex[:16]
        secret = secret or uuid.uuid4().hex
        record = {
            "id": webhook_id,
            "url": url,
            "name": name or "",
            "secret": secret,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._memory[webhook_id] = record
        r = await self._client()
        if r is not None:
            try:
                await r.set(
                    f"{WEBHOOK_REDIS_PREFIX}{webhook_id}",
                    json.dumps(record),
                    ex=60 * 60 * 24 * 365,
                )
            except Exception as e:
                log.warning("webhook_redis_store_failed: %s", e)
        log.info("webhook_registered id=%s url=%s", webhook_id, url)
        return {"webhook_id": webhook_id, "secret": secret, "url": url, "name": record["name"]}

    async def unregister(self, webhook_id: str) -> bool:
        removed = self._memory.pop(webhook_id, None)
        r = await self._client()
        if r is not None:
            try:
                key = f"{WEBHOOK_REDIS_PREFIX}{webhook_id}"
                if await r.delete(key):
                    removed = removed or {}
            except Exception as e:
                log.warning("webhook_redis_delete_failed: %s", e)
        return removed is not None

    async def list(self) -> list[dict[str, Any]]:
        r = await self._client()
        if r is not None:
            try:
                keys = await r.keys(f"{WEBHOOK_REDIS_PREFIX}*")
                records = []
                for key in keys:
                    data = await r.get(key)
                    if data:
                        records.append(json.loads(data))
                if records:
                    # Redis is authoritative when present; merge in-memory
                    # records that have not been persisted yet.
                    seen = {rec["id"] for rec in records}
                    for rec in self._memory.values():
                        if rec["id"] not in seen:
                            records.append(rec)
                    return records
            except Exception as e:
                log.warning("webhook_redis_list_failed: %s", e)
        return list(self._memory.values())

    async def clear(self) -> None:
        """Test/dev helper — drop every registration."""
        self._memory.clear()
        r = await self._client()
        if r is not None:
            try:
                keys = await r.keys(f"{WEBHOOK_REDIS_PREFIX}*")
                if keys:
                    await r.delete(*keys)
            except Exception as e:
                log.warning("webhook_redis_clear_failed: %s", e)


async def deliver_webhooks(
    alert: CriticalAlert,
    registry: WebhookRegistry | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST the alert to every registered webhook, HMAC-signed.

    Returns ``{"delivered": n, "failed": n, "errors": [url,...]}``.
    """
    registry = registry or _webhook_registry
    hooks = await registry.list()
    payload = build_webhook_payload(alert)
    body = json.dumps(payload, default=str)
    delivered = 0
    errors: list[str] = []
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        for hook in hooks:
            url = str(hook.get("url", ""))
            secret = str(hook.get("secret", ""))
            if not url:
                continue
            try:
                resp = await http.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": sign_payload(secret, body),
                        "X-Webhook-Id": str(hook.get("id", "")),
                        "X-Webhook-Event": "critical_alert",
                        "User-Agent": "RugBrain-Realtime-Alerts/1.0",
                    },
                )
                if resp.status_code < 300:
                    delivered += 1
                else:
                    errors.append(f"{url} (HTTP {resp.status_code})")
            except Exception as e:
                errors.append(f"{url} ({str(e)[:80]})")
    finally:
        if own_client:
            await http.aclose()
    if errors:
        log.warning("webhook_delivery_partial delivered=%d failed=%d", delivered, len(errors))
    return {"delivered": delivered, "failed": len(errors), "errors": errors}


_webhook_registry = WebhookRegistry()


def get_webhook_registry() -> WebhookRegistry:
    """Return the process-wide webhook registry (shared with the API)."""
    return _webhook_registry


# ── ClickHouse critical_alerts ledger ─────────────────────────────────────


class CriticalAlertLogger:
    """Best-effort ClickHouse logger for ``critical_alerts``.

    Mirrors ``rugbrain.alerts.ClickHouseAlertLogger``: lazy import, tries
    the configured DB then the fleet-writable fallback, degrades to a
    warning log when ClickHouse is unreachable.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS {db}.{table} (
        alert_id String,
        title String,
        severity String,
        chain String,
        source String,
        amount_usd Float64,
        description String,
        tx_hash String,
        addresses String,
        url String,
        detected_at DateTime('UTC'),
        raw String
    ) ENGINE = ReplacingMergeTree()
    ORDER BY (alert_id)
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._db: str | None = None

    def _get(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            ch = importlib.import_module("clickhouse_connect")
            attempts = [CLICKHOUSE_PORT]
            if CLICKHOUSE_PORT != 8123:
                attempts.append(8123)
            client: Any = None
            last_err: Exception | None = None
            for port in attempts:
                try:
                    client = ch.get_client(
                        host=CLICKHOUSE_HOST,
                        port=port,
                        interface=CLICKHOUSE_INTERFACE,
                        username=CLICKHOUSE_USER,
                        password=CLICKHOUSE_PASSWORD,
                        database=CLICKHOUSE_DATABASE,
                    )
                    for db in (CLICKHOUSE_DATABASE, CLICKHOUSE_FALLBACK_DATABASE):
                        try:
                            client.command(self.DDL.format(db=db, table=CRITICAL_ALERTS_TABLE))
                            self._db = db
                            break
                        except Exception as e:  # try next DB on failure
                            last_err = e
                            client = None
                    if self._db is not None:
                        break
                except Exception as e:  # try next port on failure
                    last_err = e
                    client = None
            if self._db is None:
                raise RuntimeError(f"clickhouse connect failed: {last_err}")
            self._client = client
            log.info(
                "critical_alerts_ready table=%s db=%s",
                CRITICAL_ALERTS_TABLE,
                self._db,
            )
        except Exception as e:
            log.warning("critical_alerts_unavailable: %s", e)
            self._client = False
        return self._client if self._client is not False else None

    def log(self, alert: CriticalAlert) -> bool:
        client = self._get()
        if client is None or self._db is None:
            return False
        try:
            client.insert(
                f"{self._db}.{CRITICAL_ALERTS_TABLE}",
                [
                    [
                        alert.id,
                        alert.title[:500],
                        alert.severity,
                        alert.chain,
                        alert.source,
                        float(alert.amount_usd or 0.0),
                        alert.description[:2000],
                        alert.tx_hash,
                        json.dumps(alert.addresses)[:2000],
                        alert.url[:2000],
                        alert.detected_at,
                        json.dumps(alert.raw, default=str)[:10000],
                    ]
                ],
                column_names=[
                    "alert_id",
                    "title",
                    "severity",
                    "chain",
                    "source",
                    "amount_usd",
                    "description",
                    "tx_hash",
                    "addresses",
                    "url",
                    "detected_at",
                    "raw",
                ],
            )
            return True
        except Exception as e:
            log.warning("critical_alerts_insert_failed: %s", e)
            return False


_critical_logger = CriticalAlertLogger()


# ── Qdrant live_threats storage ───────────────────────────────────────────


async def _embed_critical(text: str) -> list[float] | None:
    """Embed through Qwen3-4B (L2-normalized). None on failure."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{INFINITY_URL}/embeddings",
                json={"model": INFINITY_MODEL, "input": [text[:8000]]},
            )
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            if not data:
                return None
            vec = data[0].get("embedding", [])
            if not isinstance(vec, list) or not vec:
                return None
            arr = [float(x) for x in vec[:EMBEDDING_DIM]]
            norm = sum(x * x for x in arr) ** 0.5
            if norm > 0:
                arr = [x / norm for x in arr]
            return arr
    except Exception as e:
        log.debug("live_threat_embed_failed: %s", e)
        return None


async def _ensure_live_collection(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get(f"/collections/{LIVE_THREATS_COLLECTION}")
        if r.status_code == 200:
            return True
    except Exception as e:
        log.debug("live_collection_probe_failed: %s", e)
    try:
        r = await client.put(
            f"/collections/{LIVE_THREATS_COLLECTION}",
            json={"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}},
        )
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning("live_collection_create_failed: %s", e)
        return False


async def store_qdrant_live_threat(alert: CriticalAlert) -> bool:
    """Upsert *alert* into Qdrant ``live_threats`` (zero-vector fallback)."""
    try:
        embedding = await _embed_critical(alert.text())
        if not embedding:
            # Keep the point in the collection even when llama.cpp is down —
            # the payload still carries full threat context.
            embedding = [0.0] * EMBEDDING_DIM
        async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30.0) as client:
            if not await _ensure_live_collection(client):
                return False
            payload = {
                "points": [
                    {
                        "id": alert.id,
                        "vector": embedding,
                        "payload": {
                            "content": alert.text()[:10000],
                            "metadata": build_webhook_payload(alert),
                            "source": alert.source,
                            "severity": alert.severity,
                            "chain": alert.chain,
                            "collection": LIVE_THREATS_COLLECTION,
                            "source_type": "critical_alert",
                            "status": alert.status,
                        },
                    }
                ]
            }
            r = await client.put(f"/collections/{LIVE_THREATS_COLLECTION}/points", json=payload)
            ok = r.status_code in (200, 201)
            if not ok:
                log.warning("live_threat_store_failed status=%d", r.status_code)
            return ok
    except Exception as e:
        log.warning("live_threat_store_error: %s", e)
        return False


async def scroll_live_threats(limit: int = 100) -> list[dict[str, Any]]:
    """Read active threats from Qdrant ``live_threats`` (empty on failure)."""
    try:
        async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=15.0) as client:
            if not await _ensure_live_collection(client):
                return []
            r = await client.post(
                f"/collections/{LIVE_THREATS_COLLECTION}/points/scroll",
                json={"limit": limit, "with_payload": True, "with_vector": False},
            )
            if r.status_code != 200:
                return []
            points = (r.json().get("result", {}) or {}).get("points", []) or []
            out = []
            for p in points:
                payload = (p.get("payload") or {}).get("metadata") or {}
                payload.setdefault("id", p.get("id"))
                payload.setdefault("detected_at", "")
                payload.setdefault("status", "active")
                out.append(payload)
            return out
    except Exception as e:
        log.debug("live_threats_scroll_failed: %s", e)
        return []


# ── In-memory live threat / recent store ──────────────────────────────────


class LiveThreatStore:
    """Process-local view of active + recent alerts.

    Redis is used for cross-process dedup and a durable recent-alert list;
    the in-memory structures guarantee the API answers even with Redis
    down.
    """

    def __init__(self) -> None:
        self._live: dict[str, dict[str, Any]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT)
        self._seen: set[str] = set()
        self._redis: Any = None

    async def _client(self) -> Any | None:
        if self._redis is None:
            self._redis = await _get_redis()
        return self._redis

    async def is_seen(self, alert_id: str) -> bool:
        if alert_id in self._seen:
            return True
        r = await self._client()
        if r is not None:
            try:
                key = f"{SEEN_REDIS_PREFIX}{alert_id}"
                if await r.get(key):
                    return True
                await r.set(key, "1", ex=SEEN_TTL)
            except Exception as e:
                log.debug("seen_redis_failed: %s", e)
        self._seen.add(alert_id)
        return False

    async def record(self, alert: CriticalAlert) -> bool:
        """Record an alert (dedup check inside). Returns True if new."""
        if await self.is_seen(alert.id):
            return False
        item = build_webhook_payload(alert)
        item["status"] = alert.status
        item["_expires_at"] = time.time() + LIVE_TTL_SECONDS
        self._live[alert.id] = item
        self._recent.appendleft(item)
        r = await self._client()
        if r is not None:
            try:
                await r.lpush(RECENT_REDIS_KEY, json.dumps(item, default=str))
                await r.ltrim(RECENT_REDIS_KEY, 0, MAX_RECENT - 1)
            except Exception as e:
                log.debug("recent_redis_push_failed: %s", e)
        return True

    def live_threats(self) -> list[dict[str, Any]]:
        now = time.time()
        stale = [aid for aid, item in self._live.items() if item.get("_expires_at", 0) < now]
        for aid in stale:
            self._live.pop(aid, None)
        items = [dict(item) for item in self._live.values()]
        items.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
        for item in items:
            item.pop("_expires_at", None)
        return items

    def recent_alerts(self, minutes: int) -> list[dict[str, Any]]:
        cutoff = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
        items = [dict(item) for item in self._recent]
        items.sort(key=lambda x: x.get("detected_at", ""), reverse=True)
        return [item for item in items if item.get("detected_at", "") >= cutoff]

    def clear(self) -> None:
        """Test/dev helper — drop process-local state (Redis dedup stays)."""
        self._live.clear()
        self._recent.clear()
        self._seen.clear()


_store = LiveThreatStore()


def get_live_threats() -> list[dict[str, Any]]:
    """API helper: current active threats (process-local store)."""
    return _store.live_threats()


def get_recent_alerts(minutes: int = 60) -> list[dict[str, Any]]:
    """API helper: alerts detected in the last *minutes* (process-local store)."""
    return _store.recent_alerts(minutes)


# ── Telegram ──────────────────────────────────────────────────────────────


async def _send_telegram(alert: CriticalAlert) -> bool:
    """Push the exploit to the configured Telegram channel/group."""
    text = (
        f"\U0001f6a8 CRITICAL EXPLOIT ALERT\n"
        f"{alert.title}\n"
        f"Chain: {alert.chain} | Source: {alert.source}\n"
        f"Amount: ${alert.amount_usd:,.0f}\n"
        f"{alert.description[:600]}"
    )
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": text[:4000],
                        "disable_web_page_preview": True,
                    },
                )
                ok = r.status_code == 200
                if not ok:
                    log.warning("telegram_send_failed status=%d", r.status_code)
                return ok
        except Exception as e:
            log.warning("telegram_send_error: %s", e)
            return False
    # Fallback: enqueue for the existing bot (drained by rmi-backend).
    try:
        redis_client = await _get_redis()
        if redis_client is not None:
            await redis_client.lpush(
                "rag:telegram_queue", json.dumps({"text": text[:4000], "global": True})
            )
            return True
    except Exception as e:
        log.debug("telegram_queue_failed: %s", e)
    return False


# ── Detection monitors ────────────────────────────────────────────────────


def _searxng_account_query(account: str) -> str:
    """Search query targeting one security account."""
    return f"from:{account}"


async def poll_searxng_exploits(
    client: httpx.AsyncClient,
    accounts: tuple[str, ...] | None = None,
) -> list[CriticalAlert]:
    """Poll the tracked security accounts via SearXNG (RSS, JSON fallback).

    Every account is queried for its most recent posts. Items whose text
    looks like an exploit become candidate alerts. Fails open — a dead
    SearXNG instance yields an empty list.
    """
    accounts = accounts or EXPLOIT_ACCOUNTS
    candidates: list[CriticalAlert] = []
    for account in accounts:
        params = {
            "q": _searxng_account_query(account),
            "format": "rss",
            "safesearch": "0",
            "time_range": "day",
        }
        items: list[tuple[str, str, str, str]] = []
        try:
            r = await client.get(
                f"{SEARXNG_URL}/search",
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code == 200 and r.text.strip() and "<item" in r.text:
                from rugbrain.rss_parser import parse_feed

                _feed_title, feed_items = parse_feed(r.text)
                items = [
                    (
                        str(it.get("title", "")),
                        str(it.get("url", "")),
                        str(it.get("published", "")),
                        str(it.get("summary", "")),
                    )
                    for it in feed_items
                ]
            else:
                # Some SearXNG builds disable RSS — fall back to JSON.
                r2 = await client.get(
                    f"{SEARXNG_URL}/search",
                    params={**params, "format": "json"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r2.status_code == 200:
                    results = (r2.json() or {}).get("results", []) or []
                    items = [
                        (
                            str(res.get("title", "")),
                            str(res.get("url", "")),
                            "",
                            str(res.get("content", "")),
                        )
                        for res in results
                        if isinstance(res, dict)
                    ]
        except Exception as e:
            log.debug("searxng_poll_failed account=%s err=%s", account, str(e)[:120])
            continue

        for title, url, published, summary in items:
            text = f"{title} {summary}"
            if not is_exploit_candidate(text):
                continue
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"[{account}] {title[:180]}",
                    description=summary[:800],
                    url=url,
                    source="searxng",
                    severity="critical"
                    if any(k in text.lower() for k in _CRITICAL_KEYWORDS)
                    else "high",
                    raw={"account": account, "published": published},
                )
            )
    return candidates


_DEX_WHALE_SQL = """
SELECT chain, block_timestamp, tx_hash, dex, contract_address AS pair,
       token0, token1, sender, recipient, volume_usd
FROM __DB__.dex_events
WHERE volume_usd >= {threshold:Float64}
  AND block_timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
ORDER BY volume_usd DESC
LIMIT 10
"""

_NEW_ADDRESS_SQL = """
SELECT d.chain, d.block_timestamp, d.tx_hash, d.dex, d.contract_address AS pair,
       d.token0, d.token1, d.sender, d.recipient, d.volume_usd
FROM __DB__.dex_events d
WHERE d.volume_usd >= {threshold:Float64}
  AND d.block_timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
  AND (
    SELECT count()
    FROM __DB__.dex_events h
    WHERE h.recipient = d.recipient AND h.block_timestamp < d.block_timestamp
  ) <= {new_max:UInt32}
ORDER BY d.volume_usd DESC
LIMIT 10
"""

_ORACLE_PATTERN_SQL = """
SELECT chain, contract_address AS pair, token0, token1,
       count() AS whale_swaps,
       sum(volume_usd) AS cum_volume_usd,
       max(block_timestamp) AS last_seen
FROM __DB__.dex_events
WHERE whale = 1
  AND block_timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
GROUP BY chain, contract_address, token0, token1
HAVING count() >= {whale_min:UInt32}
   AND sum(volume_usd) >= {volume_usd:Float64}
ORDER BY cum_volume_usd DESC
LIMIT 10
"""


def _dex_whale_sql(db: str) -> str:
    # db is operator config (CLICKHOUSE_DATABASE env), never user input.
    return _DEX_WHALE_SQL.replace("__DB__", db)


def _new_address_sql(db: str) -> str:
    # Fresh-recipient whale swaps: recipient has few prior ledger rows.
    return _NEW_ADDRESS_SQL.replace("__DB__", db)


def _oracle_pattern_sql(db: str) -> str:
    # Oracle-manipulation proxy: a single pair sustaining many whale swaps
    # in a short window implies price-impact abuse.
    return _ORACLE_PATTERN_SQL.replace("__DB__", db)


async def poll_onchain_anomalies(
    ch: Any | None = None,
    db: str | None = None,
) -> list[CriticalAlert]:
    """Query ClickHouse for large transfers / DEX whale / oracle patterns.

    Returns candidate alerts; ``[]`` when ClickHouse is unreachable or the
    indexer tables do not exist yet.
    """
    candidates: list[CriticalAlert] = []
    try:
        if ch is None:
            ch_mod = importlib.import_module("clickhouse_connect")
            ch = ch_mod.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                interface=CLICKHOUSE_INTERFACE,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
                database=CLICKHOUSE_DATABASE,
            )
        db = db or CLICKHOUSE_FALLBACK_DATABASE
        params = {
            "threshold": LARGE_TX_USD,
            "minutes": ONCHAIN_LOOKBACK_MINUTES,
            "new_max": NEW_ADDRESS_MAX_TXS,
            "whale_min": ORACLE_WHALE_MIN,
            "volume_usd": ORACLE_VOLUME_USD,
        }
        settings = {"max_execution_time": 20}

        rows = ch.query(_dex_whale_sql(db), parameters=params, settings=settings).result_rows
        for row in rows:
            chain, _ts, tx_hash, dex, pair, t0, t1, sender, recipient, vol = row
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large DEX swap on {chain} ({dex})",
                    description=(
                        f"Whale swap {vol:,.0f} USDC-equivalent on {chain} "
                        f"pair {pair} (tokens {t0}/{t1}). Sender {sender} -> recipient {recipient}."
                    ),
                    source="clickhouse-dex",
                    chain=str(chain),
                    amount_usd=float(vol),
                    tx_hash=str(tx_hash),
                    addresses=[str(sender), str(recipient)],
                    raw={"pair": str(pair), "dex": str(dex)},
                )
            )

        rows = ch.query(_new_address_sql(db), parameters=params, settings=settings).result_rows
        for row in rows:
            chain, _ts, tx_hash, dex, pair, t0, t1, sender, recipient, vol = row
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large transfer to new address on {chain}",
                    description=(
                        f"{vol:,.0f} USDC-equivalent moved to a fresh recipient "
                        f"{recipient} (pair {pair}, {dex})."
                    ),
                    source="clickhouse-new-address",
                    chain=str(chain),
                    amount_usd=float(vol),
                    tx_hash=str(tx_hash),
                    addresses=[str(sender), str(recipient)],
                    raw={"pair": str(pair), "dex": str(dex)},
                )
            )

        rows = ch.query(_oracle_pattern_sql(db), parameters=params, settings=settings).result_rows
        for row in rows:
            chain, pair, t0, t1, swaps, cum, _last = row
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Oracle-manipulation pattern on {chain} pair {pair}",
                    description=(
                        f"{swaps} whale swaps totalling {cum:,.0f} USDC-equivalent "
                        f"on {chain} {pair} in {ONCHAIN_LOOKBACK_MINUTES} minutes — "
                        "possible price-impact abuse."
                    ),
                    source="clickhouse-oracle",
                    chain=str(chain),
                    amount_usd=float(cum),
                    raw={"pair": str(pair), "whale_swaps": int(swaps), "tokens": f"{t0}/{t1}"},
                )
            )
    except Exception as e:
        log.warning("onchain_anomaly_poll_failed: %s", str(e)[:200])
    return candidates


async def _rpc_call(client: httpx.AsyncClient, url: str, method: str, params: list[Any]) -> Any:
    r = await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Content-Type": "application/json"},
    )
    if r.status_code != 200:
        raise RuntimeError(f"rpc_http_{r.status_code}")
    result = r.json().get("result")
    if result is None:
        err = (r.json() or {}).get("error") or {}
        raise RuntimeError(f"rpc_error_{err.get('code', 'unknown')}")
    return result


async def poll_ethereum_rpc(client: httpx.AsyncClient) -> list[CriticalAlert]:
    """Large native-value transactions on the latest Ethereum block."""
    if not ETH_RPC_URL:
        return []
    threshold_wei = ETH_LARGE_TX_WEI
    if ETH_PRICE_USD > 0:
        threshold_wei = LARGE_TX_USD / ETH_PRICE_USD * 1e18
    if threshold_wei <= 0:
        return []
    candidates: list[CriticalAlert] = []
    try:
        block_hex = await _rpc_call(client, ETH_RPC_URL, "eth_blockNumber", [])
        block = await _rpc_call(client, ETH_RPC_URL, "eth_getBlockByNumber", [block_hex, True])
        txs = (block or {}).get("transactions", []) or []
        for tx in txs[:RPC_MAX_TXS]:
            try:
                value_wei = int(tx.get("value") or "0x0", 16)
            except (ValueError, TypeError):
                continue
            if value_wei < threshold_wei:
                continue
            usd = value_wei * ETH_PRICE_USD / 1e18 if ETH_PRICE_USD > 0 else 0.0
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large ETH transfer ({usd:,.0f} USD)",
                    description=(
                        f"Native transfer of {value_wei / 1e18:.2f} ETH in block "
                        f"{int(block_hex, 16)}. From {tx.get('from')} to {tx.get('to')}."
                    ),
                    source="rpc-ethereum",
                    chain="ethereum",
                    amount_usd=usd,
                    tx_hash=str(tx.get("hash", "")),
                    addresses=[str(tx.get("from", "")), str(tx.get("to", ""))],
                    raw={"block": block_hex},
                )
            )
    except Exception as e:
        log.warning("ethereum_rpc_poll_failed: %s", str(e)[:160])
    return candidates


async def poll_solana_rpc(client: httpx.AsyncClient) -> list[CriticalAlert]:
    """Large balance deltas on the latest Solana block."""
    if not SOLANA_RPC_URL:
        return []
    threshold_lamports = SOLANA_LARGE_TX_LAMPORTS
    if SOL_PRICE_USD > 0:
        threshold_lamports = LARGE_TX_USD / SOL_PRICE_USD * 1e9
    if threshold_lamports <= 0:
        return []
    candidates: list[CriticalAlert] = []
    try:
        slot = await _rpc_call(client, SOLANA_RPC_URL, "getSlot", [])
        block = await _rpc_call(
            client,
            SOLANA_RPC_URL,
            "getBlock",
            [
                slot,
                {
                    "encoding": "json",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        txs = (block or {}).get("transactions", []) or []
        for tx in txs[:RPC_MAX_TXS]:
            meta = tx.get("meta") or {}
            pre = meta.get("preBalances") or []
            post = meta.get("postBalances") or []
            if not pre or not post:
                continue
            try:
                max_delta = max(abs(int(a) - int(b)) for a, b in zip(pre, post, strict=False))
            except (ValueError, TypeError):
                continue
            if max_delta < threshold_lamports:
                continue
            usd = max_delta * SOL_PRICE_USD / 1e9 if SOL_PRICE_USD > 0 else 0.0
            sigs = (tx.get("transaction") or {}).get("signatures") or []
            candidates.append(
                CriticalAlert.from_candidate(
                    title=f"Large SOL movement ({usd:,.0f} USD)",
                    description=(
                        f"Account balance delta of {max_delta / 1e9:.2f} SOL in slot {slot}."
                    ),
                    source="rpc-solana",
                    chain="solana",
                    amount_usd=usd,
                    tx_hash=str(sigs[0]) if sigs else "",
                    raw={"slot": slot},
                )
            )
    except Exception as e:
        log.warning("solana_rpc_poll_failed: %s", str(e)[:160])
    return candidates


async def poll_rpc_large_txs(client: httpx.AsyncClient) -> list[CriticalAlert]:
    """Ethereum + Solana large-transaction candidates (single pass)."""
    candidates: list[CriticalAlert] = []
    if ETH_RPC_URL:
        candidates.extend(await poll_ethereum_rpc(client))
    if SOLANA_RPC_URL:
        candidates.extend(await poll_solana_rpc(client))
    return candidates


# ── Orchestrator ──────────────────────────────────────────────────────────


class ExploitMonitor:
    """Schedules the three monitors and runs the alert pipeline."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._stats: dict[str, Any] = {
            "started_at": "",
            "last_twitter_poll": "",
            "last_onchain_poll": "",
            "last_rpc_poll": "",
            "alerts_fired": 0,
            "alerts_deduped": 0,
            "errors": 0,
            "pipeline": {"nats": 0, "clickhouse": 0, "telegram": 0, "qdrant": 0, "webhooks": 0},
        }
        self._last_poll: dict[str, float] = {"twitter": 0.0, "onchain": 0.0, "rpc": 0.0}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    async def _handle_candidate(self, alert: CriticalAlert) -> None:
        if await _store.record(alert):
            self._stats["alerts_fired"] += 1
        else:
            self._stats["alerts_deduped"] += 1
            return
        log.warning(
            "CRITICAL_ALERT id=%s source=%s chain=%s title=%s amount_usd=%s",
            alert.id,
            alert.source,
            alert.chain,
            alert.title,
            alert.amount_usd,
        )
        await self._pipeline(alert)

    async def _pipeline(self, alert: CriticalAlert) -> None:
        # 1. NATS (Redis pub/sub fallback) — reuse AlertBus with the critical subject.
        try:
            bus = AlertBus(subject=CRITICAL_NATS_SUBJECT)
            if await bus.connect():
                ok = await bus.publish_payload(alert.model_dump())
                if ok:
                    self._stats["pipeline"]["nats"] += 1
            await bus.close()
        except Exception as e:
            log.warning("pipeline_nats_failed: %s", e)

        # 2. ClickHouse critical_alerts ledger.
        try:
            if _critical_logger.log(alert):
                self._stats["pipeline"]["clickhouse"] += 1
        except Exception as e:
            log.warning("pipeline_clickhouse_failed: %s", e)

        # 3. Telegram.
        try:
            if await _send_telegram(alert):
                self._stats["pipeline"]["telegram"] += 1
        except Exception as e:
            log.warning("pipeline_telegram_failed: %s", e)

        # 4. Qdrant live_threats.
        try:
            if await store_qdrant_live_threat(alert):
                self._stats["pipeline"]["qdrant"] += 1
        except Exception as e:
            log.warning("pipeline_qdrant_failed: %s", e)

        # 5. Community webhooks.
        try:
            result = await deliver_webhooks(alert, client=self.client)
            self._stats["pipeline"]["webhooks"] += result["delivered"]
        except Exception as e:
            log.warning("pipeline_webhooks_failed: %s", e)

    async def _poll_source(self, name: str) -> None:
        now = time.time()
        interval = {
            "twitter": TWITTER_POLL_SECONDS,
            "onchain": ONCHAIN_POLL_SECONDS,
            "rpc": RPC_POLL_SECONDS,
        }[name]
        if now - self._last_poll[name] < interval:
            return
        self._last_poll[name] = now
        candidates: list[CriticalAlert] = []
        try:
            if name == "twitter":
                candidates = await poll_searxng_exploits(self.client)
                self._stats["last_twitter_poll"] = datetime.now(UTC).isoformat()
            elif name == "onchain":
                candidates = await poll_onchain_anomalies()
                self._stats["last_onchain_poll"] = datetime.now(UTC).isoformat()
            elif name == "rpc":
                candidates = await poll_rpc_large_txs(self.client)
                self._stats["last_rpc_poll"] = datetime.now(UTC).isoformat()
        except Exception as e:
            self._stats["errors"] += 1
            log.warning("monitor_poll_error source=%s err=%s", name, str(e)[:160])
            return
        for candidate in candidates:
            await self._handle_candidate(candidate)

    async def run_forever(self) -> None:
        self._stats["started_at"] = datetime.now(UTC).isoformat()
        log.info(
            "realtime_exploit_monitor: starting (twitter=%ss onchain=%ss rpc=%ss)",
            TWITTER_POLL_SECONDS,
            ONCHAIN_POLL_SECONDS,
            RPC_POLL_SECONDS,
        )
        while True:
            for name in ("twitter", "onchain", "rpc"):
                await self._poll_source(name)
            await asyncio.sleep(MONITOR_TICK)

    def stats(self) -> dict[str, Any]:
        return dict(self._stats)


_monitor = ExploitMonitor()
_monitor_task: asyncio.Task[Any] | None = None


async def start_realtime_monitor() -> bool:
    """Start the background exploit monitor (idempotent)."""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return True
    _monitor_task = asyncio.create_task(_monitor.run_forever(), name="realtime-exploit-monitor")
    log.info("realtime_exploit_monitor: task created")
    return True


async def stop_realtime_monitor() -> None:
    global _monitor_task
    task = _monitor_task
    _monitor_task = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    await _monitor.close()


def is_realtime_monitor_running() -> bool:
    return _monitor_task is not None and not _monitor_task.done()


def realtime_monitor_stats() -> dict[str, Any]:
    return _monitor.stats()


# ── Backward-compatible watchlist classes (kept from the original module) ─


class WatchlistManager:
    """Manage user watchlists and match new scam patterns."""

    @staticmethod
    async def add_watch(
        address: str, user_id: str, channels: list[str] | None = None
    ) -> dict[str, Any]:
        channels = channels or ["websocket"]
        key = f"rag:watch:{user_id}:{address}"
        watch = {
            "address": address.lower(),
            "user_id": user_id,
            "channels": channels,
            "added_at": datetime.now(UTC).isoformat(),
        }
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                await r.set(key, json.dumps(watch))
        except Exception as e:
            log.warning("add_watch: redis failed: %s", e)
        return watch

    @staticmethod
    async def remove_watch(address: str, user_id: str) -> bool:
        key = f"rag:watch:{user_id}:{address.lower()}"
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                await r.delete(key)
                return True
        except Exception as e:
            log.warning("remove_watch: redis failed: %s", e)
        return False

    @staticmethod
    async def get_watches(user_id: str) -> list[dict[str, Any]]:
        watches = []
        pattern = f"rag:watch:{user_id}:*"
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                keys = await r.keys(pattern)
                for key in keys:
                    data = await r.get(key)
                    if data:
                        watches.append(json.loads(data))
        except Exception as e:
            log.warning("get_watches: redis failed: %s", e)
        return watches

    @staticmethod
    async def match_alert(scam_doc: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
        """Check new scam against all active watches. Returns [(watch, alert)]."""
        matches = []
        scam_addresses = set()
        if scam_doc.get("address"):
            scam_addresses.add(scam_doc["address"].lower())
        if scam_doc.get("metadata", {}).get("contract_addresses"):
            for addr in scam_doc["metadata"]["contract_addresses"]:
                scam_addresses.add(addr.lower())
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                keys = await r.keys("rag:watch:*")
                for key in keys:
                    addr = key.decode().split(":")[-1]
                    if addr in scam_addresses:
                        data = await r.get(key)
                        if data:
                            watch = json.loads(data)
                            alert = {
                                "type": "scam_detected",
                                "address": scam_doc.get("address", addr),
                                "chain": scam_doc.get("chain", "unknown"),
                                "scam_type": scam_doc.get("scam_type", "unknown"),
                                "risk": scam_doc.get("severity", "medium"),
                                "summary": scam_doc.get("summary", "New scam pattern detected"),
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            matches.append((watch, alert))
        except Exception as e:
            log.warning("match_alert: redis failed: %s", e)
        return matches


class AlertDispatcher:
    @staticmethod
    async def dispatch(watch: dict[str, Any], alert: dict[str, Any]) -> None:
        channels = watch.get("channels", ["websocket"])
        for channel in channels:
            try:
                if channel == "websocket":
                    await AlertDispatcher._dispatch_websocket(watch["user_id"], alert)
                elif channel == "telegram":
                    await AlertDispatcher._dispatch_telegram(watch["user_id"], alert)
            except Exception as e:
                log.warning("dispatch %s failed: %s", channel, e)

    @staticmethod
    async def _dispatch_websocket(user_id: str, alert: dict[str, Any]) -> None:
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                channel = f"rag:alerts:{user_id}"
                await r.publish(channel, json.dumps(alert))
                log.info("alert websocket user=%s address=%s", user_id, alert.get("address"))
        except Exception as e:
            log.warning("websocket dispatch failed: %s", e)

    @staticmethod
    async def _dispatch_telegram(user_id: str, alert: dict[str, Any]) -> None:
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                msg = (
                    f"\U0001f6a8 *RMI Scam Alert*\n"
                    f"Address: `{alert.get('address')}`\n"
                    f"Type: {alert.get('scam_type')}\n"
                    f"Risk: {alert.get('risk')}\n"
                    f"Summary: {alert.get('summary')}\n"
                    f"Chain: {alert.get('chain')}"
                )
                await r.lpush("rag:telegram_queue", json.dumps({"user_id": user_id, "text": msg}))
        except Exception as e:
            log.warning("telegram dispatch failed: %s", e)


async def start_alert_listener() -> None:
    """Background task: listen for new scams, check against watchlists, dispatch."""
    log.info("rag_alert_listener: starting")
    while True:
        try:
            from rugbrain.compat import get_redis

            r = get_redis()
            if r:
                pubsub = r.pubsub()
                pubsub.subscribe("rag:new_scam")
                for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            scam_doc = json.loads(message["data"])
                            matches = await WatchlistManager.match_alert(scam_doc)
                            for watch, alert in matches:
                                await AlertDispatcher.dispatch(watch, alert)
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            log.warning("alert_listener_error: %s — retrying in 5s", e)
            await asyncio.sleep(5)


# ── CLI ───────────────────────────────────────────────────────────────────


async def _run_once() -> None:
    client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    try:
        all_candidates: list[CriticalAlert] = []
        all_candidates.extend(await poll_searxng_exploits(client))
        all_candidates.extend(await poll_onchain_anomalies())
        all_candidates.extend(await poll_rpc_large_txs(client))
        for alert in all_candidates:
            await _monitor._handle_candidate(alert)
        print(
            json.dumps(
                {
                    "candidates": len(all_candidates),
                    "fired": _monitor.stats()["alerts_fired"],
                    "stats": _monitor.stats(),
                },
                indent=2,
            )
        )
    finally:
        await client.aclose()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="RugBrain real-time exploit monitor")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args()
    if args.once:
        asyncio.run(_run_once())
    else:
        asyncio.run(_monitor.run_forever())


if __name__ == "__main__":
    main()

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
