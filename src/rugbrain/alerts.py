"""RugBrain — real-time crypto security alert ingestion.

Polls free security-intel sources every ``ALERT_POLL_INTERVAL`` (default
300s) and, whenever a NEW incident is detected:

  1. embeds the incident description (Infinity BAAI/bge-m3, 1024-dim)
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
    SpreekAway) via Nitter RSS, falling back to SearXNG (Talos:8089)

Rate limiting: each source keeps a ``last_poll`` timestamp in Redis
(``rag:alerts:last_poll:<source>``) and is only polled when the minimum
interval has elapsed. Individual sources can be disabled with
``ALERT_<SOURCE>_ENABLED=false`` (e.g. ``ALERT_TWITTER_ENABLED=false``).

Usage::

    python -m rugbrain.alerts --once        # single poll pass (cron friendly)
    python -m rugbrain.alerts               # long-running poller (every 60s tick)

Requires (all optional, degrade gracefully when absent):
  - Qdrant (QDRANT_URL) + Infinity (INFINITY_URL) for embedding/storage
  - Redis (REDIS_HOST) for rate-limit + dedup state
  - NATS (NATS_URL) — optional; Redis pub/sub is the fallback bus
  - ClickHouse (CLICKHOUSE_HOST/PORT/USER/PASSWORD/DATABASE) — optional;
    skipped with a warning when unreachable
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np
from pydantic import BaseModel, Field

log = logging.getLogger("rugbrain.alerts")

# ── Configuration ────────────────────────────────────────────────────────

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
INFINITY_URL = os.getenv("INFINITY_URL", "http://localhost:8001").rstrip("/")
INFINITY_MODEL = os.getenv("RAG_INFINITY_EMBED_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = int(os.getenv("INFINITY_EMBED_DIMS", "1024"))

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

# ── Models ───────────────────────────────────────────────────────────────


class SecurityAlert(BaseModel):
    """A single detected crypto security incident.

    ``id`` is a deterministic UUIDv5 derived from ``source + title + date`` so
    re-detection of the same incident maps to the same Qdrant point (upsert is
    idempotent). ``detected_at`` records when *we* first saw it.
    """

    source: str
    title: str
    description: str = ""
    url: str = ""
    severity: str = "high"
    attack_vector: str = "unknown"
    chain: str = "unknown"
    protocol: str = ""
    amount_lost_usd: float = 0.0
    date: str = ""
    detected_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        seed = f"alert:{self.source}:{self.title.strip().lower()}:{self.date}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))

    def text(self) -> str:
        """Render the incident as embeddable text."""
        parts = [
            f"Security alert: {self.title}",
            f"Source: {self.source}",
            f"Date: {self.date or 'unknown'}",
            f"Protocol: {self.protocol or 'unknown'}",
            f"Chain: {self.chain or 'unknown'}",
            f"Attack vector: {self.attack_vector or 'unknown'}",
            f"Amount lost: ${self.amount_lost_usd:,.0f}"
            if self.amount_lost_usd
            else "Amount lost: unknown",
        ]
        if self.description:
            parts.append(f"Details: {self.description}")
        if self.url:
            parts.append(f"URL: {self.url}")
        return "\n".join(parts)


# ── State backends (Redis with in-memory fallback) ───────────────────────


class AlertState:
    """Rate-limit + dedup state.

    Uses Redis when reachable (survives restarts, shared across processes);
    falls back to an in-memory dict so the poller still works in tests /
    minimal environments.
    """

    LAST_POLL_PREFIX = "rag:alerts:last_poll:"
    SEEN_PREFIX = "rag:alerts:seen:"

    def __init__(self) -> None:
        self._redis: Any = None
        self._memory: dict[str, Any] = {}
        self._redis_ok = False

    async def _client(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                decode_responses=True,
            )
            await r.ping()
            self._redis = r
            self._redis_ok = True
        except Exception as e:
            log.debug("alert_state_redis_unavailable: %s", e)
            self._redis = False
        return self._redis

    async def last_poll(self, source: str) -> float | None:
        c = await self._client()
        if c:
            try:
                v = await c.get(f"{self.LAST_POLL_PREFIX}{source}")
                return float(v) if v else None
            except Exception:
                return None
        return self._memory.get(f"poll:{source}")

    async def set_last_poll(self, source: str, ts: float) -> None:
        c = await self._client()
        if c:
            try:
                await c.set(f"{self.LAST_POLL_PREFIX}{source}", str(ts))
            except Exception as e:
                log.debug("alert_state_set_poll_failed: %s", e)
        self._memory[f"poll:{source}"] = ts

    async def is_seen(self, alert_id: str) -> bool:
        c = await self._client()
        if c:
            try:
                return bool(await c.exists(f"{self.SEEN_PREFIX}{alert_id}"))
            except Exception as e:
                log.debug("alert_state_seen_check_failed: %s", e)
                return False
        return alert_id in self._memory.get("seen", set())

    async def mark_seen(self, alert_id: str) -> None:
        c = await self._client()
        if c:
            try:
                await c.set(f"{self.SEEN_PREFIX}{alert_id}", "1", ex=SEEN_TTL)
            except Exception as e:
                log.debug("alert_state_mark_seen_failed: %s", e)
        self._memory.setdefault("seen", set()).add(alert_id)


def _enabled(name: str) -> bool:
    return os.getenv(f"ALERT_{name.upper()}_ENABLED", "true").lower() in ("1", "true", "yes")


def _rate_limited(state: AlertState, source: str, now: float, interval: float) -> bool:
    """Return True when *source* was polled within ``interval`` seconds.

    The poll loop uses the async ``AlertState.last_poll``; this pure helper
    backs the in-memory state used in tests / minimal environments.
    """
    last = getattr(state, "_memory", {}).get(f"poll:{source}")
    if last is None:
        return False
    return (now - float(last)) < interval


# ── Embedding ────────────────────────────────────────────────────────────


async def _embed(text: str) -> list[float] | None:
    """Embed *text* through Infinity (L2-normalized). Returns None on failure."""
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
            arr = np.asarray(vec, dtype=np.float32)
            if arr.shape[0] >= EMBEDDING_DIM:
                arr = arr[:EMBEDDING_DIM]
            else:
                arr = np.concatenate(
                    [arr, np.zeros(EMBEDDING_DIM - arr.shape[0], dtype=np.float32)]
                )
            n = float(np.linalg.norm(arr))
            if n > 0:
                arr = arr / n
            return [float(x) for x in arr]
    except Exception as e:
        log.debug("alert_embed_failed: %s", e)
        return None


# ── Qdrant storage ───────────────────────────────────────────────────────


async def _ensure_alert_collection(client: httpx.AsyncClient) -> bool:
    try:
        r = await client.get(f"/collections/{ALERT_COLLECTION}")
        if r.status_code == 200:
            return True
    except Exception as e:
        log.debug("alert_collection_probe_failed: %s", e)
    try:
        r = await client.put(
            f"/collections/{ALERT_COLLECTION}",
            json={"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}},
        )
        return r.status_code in (200, 201)
    except Exception as e:
        log.warning("alert_collection_create_failed: %s", e)
        return False


async def store_alert(alert: SecurityAlert, embedding: list[float]) -> bool:
    """Upsert *alert* into the Qdrant ``security_alerts`` collection."""
    try:
        async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30.0) as client:
            if not await _ensure_alert_collection(client):
                return False
            payload = {
                "points": [
                    {
                        "id": alert.id,
                        "vector": embedding,
                        "payload": {
                            "content": alert.text()[:10000],
                            "metadata": {
                                "source": alert.source,
                                "title": alert.title,
                                "protocol": alert.protocol,
                                "attack_vector": alert.attack_vector,
                                "chain": alert.chain,
                                "amount_lost_usd": alert.amount_lost_usd,
                                "date": alert.date,
                                "detected_at": alert.detected_at,
                                "url": alert.url,
                                "severity": alert.severity,
                            },
                            "source": alert.source,
                            "severity": alert.severity,
                            "chain": alert.chain,
                            "collection": ALERT_COLLECTION,
                            "source_type": "alert",
                        },
                    }
                ]
            }
            r = await client.put(f"/collections/{ALERT_COLLECTION}/points", json=payload)
            ok = r.status_code in (200, 201)
            if not ok:
                log.warning("alert_store_failed status=%d body=%s", r.status_code, r.text[:200])
            return ok
    except Exception as e:
        log.warning("alert_store_error: %s", e)
        return False


# ── Message bus (NATS primary, Redis pub/sub fallback) ───────────────────


class AlertBus:
    """Publish alerts to NATS ``alerts.crypto.security`` (or Redis pub/sub).

    The subject name is identical in both transports so a downstream
    consumer can subscribe to either without knowing which bus is active.
    """

    def __init__(self, subject: str | None = None) -> None:
        self._nats: Any = None
        self._nc: Any = None
        self._redis: Any = None
        self._mode = "none"
        # Allow callers (e.g. rugbrain.realtime_alerts) to publish on a
        # different subject while reusing the same transport fallback.
        self._subject = subject or NATS_SUBJECT

    async def connect(self) -> str:
        """Establish the bus connection. Returns the active transport name."""
        if NATS_URL:
            nats_mod = self._load_nats_module()
            if nats_mod is not None:
                try:
                    self._nc = await nats_mod.connect(NATS_URL, max_reconnect_attempts=3)
                    self._mode = "nats"
                    log.info("alert_bus_connected transport=nats url=%s", NATS_URL)
                    return self._mode
                except Exception as e:
                    log.warning("alert_bus_nats_connect_failed: %s", e)
            else:
                log.warning("alert_bus_nats_disabled reason=package_missing (install nats-py)")
        # Redis fallback
        try:
            import redis.asyncio as aioredis

            r = aioredis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                decode_responses=True,
            )
            await r.ping()
            self._redis = r
            self._mode = "redis"
            log.info("alert_bus_connected transport=redis subject=%s", self._subject)
        except Exception as e:
            log.warning("alert_bus_redis_failed: %s", e)
            self._mode = "none"
        return self._mode

    @staticmethod
    def _load_nats_module() -> Any | None:
        try:
            return importlib.import_module("nats")
        except Exception:
            return None

    async def publish(self, alert: SecurityAlert) -> bool:
        return await self.publish_payload(alert.model_dump())

    async def publish_payload(self, payload: dict[str, Any]) -> bool:
        """Publish an arbitrary JSON-serializable payload on the subject."""
        message = json.dumps(payload, default=str)
        if self._mode == "nats" and self._nc is not None:
            try:
                await self._nc.publish(self._subject, message.encode("utf-8"))
                return True
            except Exception as e:
                log.warning("alert_bus_nats_publish_failed: %s", e)
                return False
        if self._mode == "redis" and self._redis is not None:
            try:
                await self._redis.publish(self._subject, message)
                return True
            except Exception as e:
                log.warning("alert_bus_redis_publish_failed: %s", e)
                return False
        log.warning("alert_bus_not_connected drop=1 subject=%s", self._subject)
        return False

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception as e:
                log.debug("alert_bus_nats_close_failed: %s", e)
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception as e:
                log.debug("alert_bus_redis_close_failed: %s", e)


# ── ClickHouse historical log ────────────────────────────────────────────


class ClickHouseAlertLogger:
    """Best-effort ClickHouse logger for the historical alert ledger.

    Lazily imports ``clickhouse_connect`` so the module works without the
    package installed; every method degrades to a warning log on failure.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS {db}.{table} (
        alert_id String,
        source String,
        title String,
        protocol String,
        severity String,
        attack_vector String,
        chain String,
        amount_lost_usd Float64,
        description String,
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
                attempts.append(8123)  # fleet convention: HTTP interface on 8123
            client = None
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
                    # Try the configured DB first, then the fleet-writable
                    # fallback DB (rmi user lacks DDL on the primary).
                    for db in (CLICKHOUSE_DATABASE, CLICKHOUSE_FALLBACK_DATABASE):
                        try:
                            client.command(self.DDL.format(db=db, table=CLICKHOUSE_TABLE))
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
                "clickhouse_alerts_ready table=%s db=%s port=%d",
                CLICKHOUSE_TABLE,
                self._db,
                port,
            )
        except Exception as e:
            log.warning("clickhouse_alerts_unavailable: %s", e)
            self._client = False
        return self._client if self._client is not False else None

    def log(self, alert: SecurityAlert) -> bool:
        client = self._get()
        if client is None or self._db is None:
            return False
        try:
            client.insert(
                f"{self._db}.{CLICKHOUSE_TABLE}",
                [
                    [
                        alert.id,
                        alert.source,
                        alert.title,
                        alert.protocol,
                        alert.severity,
                        alert.attack_vector,
                        alert.chain,
                        float(alert.amount_lost_usd or 0.0),
                        alert.description,
                        alert.url,
                        alert.detected_at,
                        json.dumps(alert.raw, default=str)[:10000],
                    ]
                ],
                column_names=[
                    "alert_id",
                    "source",
                    "title",
                    "protocol",
                    "severity",
                    "attack_vector",
                    "chain",
                    "amount_lost_usd",
                    "description",
                    "url",
                    "detected_at",
                    "raw",
                ],
            )
            return True
        except Exception as e:
            log.warning("clickhouse_alerts_insert_failed: %s", e)
            return False


# ── Sources ──────────────────────────────────────────────────────────────

COINGECKO_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=gecko_desc&per_page=50&sparkline=false"
)
DEFILLAMA_HACKS_URL = "https://api.llama.fi/hacks"
REKT_LEADERBOARD_URL = "https://rekt.news/leaderboard"


def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()


def _within_window(date_iso: str, now: float, days: float) -> bool:
    if not date_iso:
        return False
    try:
        d = datetime.fromisoformat(date_iso).date()
        age_days = (datetime.fromtimestamp(now, tz=UTC).date() - d).days
        return 0 <= age_days <= days
    except ValueError:
        return False


async def poll_coingecko(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """New token launches from CoinGecko (recently-added / gecko_desc)."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(COINGECKO_MARKETS_URL)
        if r.status_code != 200:
            log.warning("coingecko_poll_failed status=%d", r.status_code)
            return alerts
        for coin in r.json() or []:
            if not isinstance(coin, dict):
                continue
            name = str(coin.get("name", "")).strip()
            symbol = str(coin.get("symbol", "")).strip().upper()
            if not name:
                continue
            price = coin.get("current_price") or 0
            mc = coin.get("market_cap") or 0
            description = (
                f"Newly listed token {name} ({symbol}) with market cap "
                f"${mc:,.0f} and current price ${price:,.6f}. "
                "New launches are frequently targeted for pump-and-dump and "
                "rug-pull schemes; verify liquidity locks and holder "
                "distribution before trading."
            )
            alerts.append(
                SecurityAlert(
                    source="coingecko",
                    title=f"New token launch: {name} ({symbol})",
                    description=description,
                    url=f"https://www.coingecko.com/en/coins/{coin.get('id', '')}",
                    severity="low",
                    attack_vector="rug-pull",
                    chain="unknown",
                    protocol=name,
                    date="",
                    raw={"coin_id": coin.get("id", ""), "symbol": symbol},
                )
            )
    except Exception as e:
        log.warning("coingecko_poll_error: %s", e)
    return alerts


async def poll_defillama(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Major exploits from the DeFiLlama hacks database (recent window)."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(DEFILLAMA_HACKS_URL)
        if r.status_code != 200:
            log.warning("defillama_poll_failed status=%d", r.status_code)
            return alerts
        for hack in r.json() or []:
            if not isinstance(hack, dict):
                continue
            ts = float(hack.get("date") or 0)
            if ts <= 0:
                continue
            date_iso = _ts_to_iso(ts)
            if not _within_window(date_iso, now, RECENT_WINDOW_DAYS):
                continue
            name = str(hack.get("name", "")).strip() or "unknown"
            amount = float(hack.get("amount") or 0)
            chains = hack.get("chain") or []
            chain = str(chains[0]).lower() if isinstance(chains, list) and chains else "unknown"
            technique = str(hack.get("technique") or "").strip()
            classification = str(hack.get("classification") or "").strip()
            vector = detect_vector(f"{technique} {classification} {name}")
            description = (
                f"{name} was exploited for ${amount:,.0f} "
                f"({technique or classification or 'unknown technique'}). "
                f"Target type: {hack.get('targetType') or 'unknown'}. "
                f"Language: {hack.get('language') or 'unknown'}."
            )
            alerts.append(
                SecurityAlert(
                    source="defillama",
                    title=f"Exploit: {name} (${amount:,.0f})",
                    description=description,
                    severity="critical" if amount >= 10_000_000 else "high",
                    attack_vector=vector,
                    chain=chain,
                    protocol=name,
                    amount_lost_usd=amount,
                    date=date_iso,
                    raw={
                        "technique": technique,
                        "classification": classification,
                        "returned_funds": hack.get("returnedFunds"),
                    },
                )
            )
    except Exception as e:
        log.warning("defillama_poll_error: %s", e)
    return alerts


async def poll_rekt(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Recent major exploits from the Rekt News leaderboard."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(REKT_LEADERBOARD_URL, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log.warning("rekt_poll_failed status=%d", r.status_code)
            return alerts
        html = r.text
        m = re.search(r'__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            m = re.search(r"__NEXT_DATA__\">(.*?)</script>", html, re.S)
        if not m:
            log.warning("rekt_poll_no_next_data")
            return alerts
        data = json.loads(m.group(1))
        entries = data.get("props", {}).get("pageProps", {}).get("leaderboard", []) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            slug = str(entry.get("slug", "")).strip()
            if not title:
                continue
            rekt = entry.get("rekt") or {}
            amount = float(rekt.get("amount") or 0)
            date_raw = rekt.get("date") or entry.get("date") or ""
            date_iso = _parse_date_iso(str(date_raw))
            if not _within_window(date_iso, now, RECENT_WINDOW_DAYS):
                continue
            tags = entry.get("tags") or []
            tags_text = ", ".join(str(t) for t in tags)
            excerpt = str(entry.get("excerpt", "")).strip()
            vector = detect_vector(f"{title} {tags_text} {excerpt}")
            chain = detect_chain(f"{title} {tags_text}")
            alerts.append(
                SecurityAlert(
                    source="rekt",
                    title=title,
                    description=f"{excerpt} Tags: {tags_text}."
                    if excerpt
                    else f"Tags: {tags_text}.",
                    url=f"https://rekt.news/{slug}/" if slug else REKT_LEADERBOARD_URL,
                    severity="critical"
                    if amount >= 10_000_000
                    else ("high" if amount else "medium"),
                    attack_vector=vector,
                    chain=chain,
                    protocol=title,
                    amount_lost_usd=amount,
                    date=date_iso,
                    raw={"slug": slug, "tags": tags},
                )
            )
    except Exception as e:
        log.warning("rekt_poll_error: %s", e)
    return alerts


async def poll_twitter(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Crypto security X accounts via Nitter RSS (SearXNG fallback).

    These accounts (CertiK, PeckShield, Beosin, SpreekAway) publish exploit
    and scam alerts as they happen. Nitter instances change frequently, so a
    failure here is logged and skipped — never fatal.
    """
    alerts: list[SecurityAlert] = []
    for account in SECURITY_ACCOUNTS:
        account = account.strip()
        if not account:
            continue
        rss_url = f"{NITTER_URL}/{account}/rss"
        try:
            r = await client.get(rss_url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or not r.text.strip():
                log.debug("nitter_empty account=%s status=%d", account, r.status_code)
                continue
            items = _parse_rss_items(r.text, account)
            for item in items:
                title, link, pub_date, summary = item
                if not _within_window(pub_date, now, RECENT_WINDOW_DAYS):
                    continue
                vector = detect_vector(f"{title} {summary}")
                alerts.append(
                    SecurityAlert(
                        source="twitter",
                        title=f"[{account}] {title[:180]}",
                        description=summary[:800],
                        url=link,
                        severity="high",
                        attack_vector=vector,
                        chain="unknown",
                        protocol="",
                        amount_lost_usd=0.0,
                        date=pub_date,
                        raw={"account": account},
                    )
                )
        except Exception as e:
            log.debug("nitter_fetch_failed account=%s err=%s", account, str(e)[:120])
    if not alerts:
        alerts = await _searxng_fallback(client, now)
    return alerts


def _parse_rss_items(xml: str, account: str) -> list[tuple[str, str, str, str]]:
    """Very small RSS parser — enough for Nitter's item/title/link/pubDate."""
    items: list[tuple[str, str, str, str]] = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        block = m.group(1)
        title = _tag_text(block, "title")
        link = _tag_text(block, "link")
        pub_date = _parse_date_iso(_tag_text(block, "pubDate"))
        summary = _tag_text(block, "description")
        if title:
            items.append((title, link, pub_date, summary))
    return items


def _tag_text(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


async def _searxng_fallback(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Fallback: query SearXNG (Talos:8089) for fresh crypto security alerts."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(
            f"{SEARXNG_URL}/search",
            params={
                "q": "crypto hack exploit alert",
                "format": "json",
                "safesearch": "0",
                "time_range": "day",
            },
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            log.debug("searxng_fallback_unavailable status=%d", r.status_code)
            return alerts
        results = (r.json() or {}).get("results", []) or []
        for res in results[:10]:
            if not isinstance(res, dict):
                continue
            title = str(res.get("title", "")).strip()
            url = str(res.get("url", "")).strip()
            content = str(res.get("content", "")).strip()
            if not title:
                continue
            vector = detect_vector(f"{title} {content}")
            alerts.append(
                SecurityAlert(
                    source="searxng",
                    title=title[:220],
                    description=content[:800],
                    url=url,
                    severity="high",
                    attack_vector=vector,
                    chain="unknown",
                    protocol="",
                    amount_lost_usd=0.0,
                    date="",
                    raw={"engine": "searxng"},
                )
            )
    except Exception as e:
        log.debug("searxng_fallback_error: %s", e)
    return alerts


# ── Vector / chain helpers (mirror rugbrain.crypto_schema, no hard import) ─

_VECTOR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"re-?entr(anc)?y", re.I), "reentrancy"),
    (re.compile(r"flash ?loan", re.I), "flash-loan"),
    (re.compile(r"oracle|price ?manipulation", re.I), "oracle-manipulation"),
    (re.compile(r"access ?control|delegatecall|tx\.origin", re.I), "access-control"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"signature", re.I), "signature-malleability"),
    (re.compile(r"overflow|underflow", re.I), "integer-overflow"),
    (re.compile(r"private ?key|seed phrase|key compromise", re.I), "key-compromise"),
    (re.compile(r"rug ?pull|liquidity removed|exit scam|pump", re.I), "rug-pull"),
    (re.compile(r"honeypot", re.I), "honeypot"),
    (re.compile(r"phish", re.I), "phishing"),
    (re.compile(r"withdraw", re.I), "withdrawal-exploit"),
    (re.compile(r"denial of service|dos", re.I), "denial-of-service"),
    (re.compile(r"front.?run|sandwich|mev", re.I), "front-running"),
    (re.compile(r"supply chain", re.I), "supply-chain"),
    (re.compile(r"input valid", re.I), "input-validation"),
    (re.compile(r"exploit|hack|vulnerab|bug", re.I), "smart-contract-exploit"),
]

_CHAIN_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("ethereum", ("ethereum", "eth ", "erc-20", "erc-721", "evm", "mainnet")),
    ("bsc", ("bsc", "binance smart chain", "bnb chain", "bep-20")),
    ("solana", ("solana", "spl")),
    ("polygon", ("polygon", "matic")),
    ("arbitrum", ("arbitrum", "arb ")),
    ("optimism", ("optimism", "op mainnet")),
    ("base", ("base",)),
    ("avalanche", ("avalanche", "avax")),
    ("tron", ("tron", "trc-20")),
    ("fantom", ("fantom", "ftm")),
    ("multi", ("multi-chain", "cross-chain", "multiple chains")),
]


def detect_vector(text: str) -> str:
    lowered = text or ""
    for pattern, vector in _VECTOR_PATTERNS:
        if pattern.search(lowered):
            return vector
    return "unknown"


def detect_chain(text: str) -> str:
    lowered = f" {text.lower()} "
    found: set[str] = set()
    for chain, aliases in _CHAIN_ALIASES:
        for alias in aliases:
            if alias in lowered:
                found.add(chain)
                break
    if re.search(r"\b0x[a-fA-F0-9]{8,}\b", lowered):
        found.add("ethereum")
    if "multi" in found:
        return "multi"
    if len(found) > 1:
        return "multi"
    return found.pop() if found else "unknown"


def _parse_date_iso(raw: str) -> str:
    """Parse pubDate (RFC-822) or ISO dates into YYYY-MM-DD (best effort)."""
    s = (raw or "").strip()
    if not s:
        return ""
    # RFC-822: "Tue, 04 Aug 2026 12:00:00 +0000"
    m = re.search(
        r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", s, re.I
    )
    if m:
        months = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        try:
            day = int(m.group(1))
            month = months[m.group(2).lower()]
            year = int(m.group(3))
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


# ── Orchestration ────────────────────────────────────────────────────────

SOURCE_POLLERS: dict[str, Any] = {
    "coingecko": poll_coingecko,
    "defillama": poll_defillama,
    "rekt": poll_rekt,
    "twitter": poll_twitter,
}


async def _run_source(
    name: str,
    client: httpx.AsyncClient,
    state: AlertState,
    bus: AlertBus,
    ch_logger: ClickHouseAlertLogger,
    now: float,
    dry_run: bool,
) -> dict[str, Any]:
    """Poll one source, ingest new alerts. Returns per-source stats."""
    stats: dict[str, Any] = {"source": name, "detected": 0, "new": 0, "skipped_dup": 0}
    poller = SOURCE_POLLERS[name]
    if not _enabled(name):
        stats["disabled"] = True
        return stats
    last = await state.last_poll(name)
    if last is not None and (now - float(last)) < POLL_INTERVAL:
        stats["rate_limited"] = True
        return stats
    await state.set_last_poll(name, now)

    try:
        alerts = await poller(client, now)
    except Exception as e:
        log.warning("source=%s poll_error: %s", name, str(e)[:200])
        stats["error"] = str(e)[:200]
        return stats

    stats["detected"] = len(alerts)
    for alert in alerts:
        if await state.is_seen(alert.id):
            stats["skipped_dup"] += 1
            continue
        await state.mark_seen(alert.id)
        stats["new"] += 1
        log.info(
            "alert_detected source=%s title=%s severity=%s amount=%s",
            alert.source,
            alert.title[:100],
            alert.severity,
            f"${alert.amount_lost_usd:,.0f}" if alert.amount_lost_usd else "unknown",
        )
        if dry_run:
            continue
        embedding = await _embed(alert.text())
        if embedding is None:
            log.warning("alert_embed_failed title=%s", alert.title[:80])
            continue
        await store_alert(alert, embedding)
        await bus.publish(alert)
        ch_logger.log(alert)
    return stats


async def poll_once(dry_run: bool = False) -> dict[str, Any]:
    """Run a single full poll pass over all sources. Returns aggregate stats."""
    state = AlertState()
    bus = AlertBus()
    ch_logger = ClickHouseAlertLogger()
    now = datetime.now(UTC).timestamp()
    results: dict[str, Any] = {"ran_at": datetime.now(UTC).isoformat(), "sources": {}}
    try:
        bus_mode = await bus.connect()
        results["bus"] = bus_mode
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for name in SOURCE_POLLERS:
                stats = await _run_source(name, client, state, bus, ch_logger, now, dry_run)
                results["sources"][name] = stats
    finally:
        await bus.close()
    results["total_new"] = sum(s.get("new", 0) for s in results["sources"].values())
    results["total_detected"] = sum(s.get("detected", 0) for s in results["sources"].values())
    return results


async def run_alert_poller(dry_run: bool = False) -> None:
    """Long-running poller: tick every TICK_INTERVAL, poll sources on their
    own rate-limit cadence."""
    log.info("alert_poller_starting interval=%ss tick=%ss", POLL_INTERVAL, TICK_INTERVAL)
    while True:
        try:
            results = await poll_once(dry_run=dry_run)
            log.info(
                "alert_poller_pass new=%d detected=%d sources=%s",
                results.get("total_new", 0),
                results.get("total_detected", 0),
                list(results["sources"].keys()),
            )
        except Exception as e:
            log.warning("alert_poller_pass_failed: %s", e)
        await asyncio.sleep(TICK_INTERVAL)


def main() -> None:
    parser = argparse.ArgumentParser(description="RugBrain security alert poller")
    parser.add_argument("--once", action="store_true", help="Run a single poll pass and exit")
    parser.add_argument("--dry-run", action="store_true", help="Detect without embedding/storing")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("RAG_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.once:
        results = asyncio.run(poll_once(dry_run=args.dry_run))
        print(json.dumps(results, indent=2, default=str))
    else:
        asyncio.run(run_alert_poller(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
