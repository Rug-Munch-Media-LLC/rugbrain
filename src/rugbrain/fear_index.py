"""
RugBrain — crypto panic / fear index.

Polls several market-sentiment signals and blends them into a single
0-100 index (0 = extreme fear, 100 = extreme greed) that feeds
rugmunch.io's news ranking and token-risk scoring:

  * ``fng``      — Alternative.me Fear & Greed index (free API)
  * ``social``   — crypto social sentiment via SearXNG (self-hosted,
                   no API key)
  * ``onchain``  — exchange net flows / whale movements / liquidations
                   from a configurable DataBus-style provider
  * ``news``     — aggregate sentiment of recently ingested news
                   (from the Qdrant ``news`` collection)

Snapshots are computed every ``FEAR_INDEX_POLL_SECONDS`` (default 300s)
by an asyncio poller started from the FastAPI lifespan, persisted to
ClickHouse ``crypto_fear_index`` (best-effort, lazy import) and cached in
Redis. Every fetch fails OPEN: a dead provider is omitted and the index
is renormalized over the remaining components, never raising into the
API layer.

The composite is exposed through:

  * ``GET /api/v1/fear-index``            — current value + window samples
  * ``GET /api/v1/fear-index/history``    — ClickHouse-backed chart data
  * ``adjust_risk_for_market``            — escalates token risk verdicts
    when the market is fearful (search/analyze scoring feed)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger("rugbrain.fear_index")

# ── Configuration ───────────────────────────────────────────────────────
FEAR_INDEX_POLL_SECONDS = int(os.getenv("FEAR_INDEX_POLL_SECONDS", "300"))
FNG_URL = os.getenv("FEAR_GREED_URL", "https://api.alternative.me/fng/?limit=1")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8089/search")
SEARXNG_QUERY = os.getenv("FEAR_SOCIAL_QUERY", "bitcoin ethereum crypto market")
ONCHAIN_SIGNALS_URL = os.getenv("ONCHAIN_SIGNALS_URL", "")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
CACHE_KEY = os.getenv("FEAR_INDEX_CACHE_KEY", "rag:fear_index:current")
CACHE_TTL = int(os.getenv("FEAR_INDEX_CACHE_TTL", str(FEAR_INDEX_POLL_SECONDS + 60)))

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

# ── Helpers ─────────────────────────────────────────────────────────────


def _clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def _parse_dt(value: Any) -> datetime | None:
    """Parse datetime/ISO string values, else None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _as_int_or_none(value: Any) -> int | None:
    """Coerce a component value to 0-100 int; None for missing/errored."""
    if value is None or isinstance(value, (Exception, bool)):
        return None
    if isinstance(value, (int, float)):
        return _clamp(float(value))
    return None


def _term_hit(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms)


# ── Classification ──────────────────────────────────────────────────────


def classify_fear(value: int) -> str:
    """Label a 0-100 index: extreme_fear|fear|neutral|greed|extreme_greed."""
    if value < 25:
        return "extreme_fear"
    if value < 40:
        return "fear"
    if value <= 60:
        return "neutral"
    if value <= 74:
        return "greed"
    return "extreme_greed"


# ── Component fetchers (all fail open → None) ───────────────────────────


async def fetch_fng(client: httpx.AsyncClient) -> int | None:
    """Alternative.me Fear & Greed value (0-100), None on any failure."""
    try:
        r = await client.get(FNG_URL, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        values = data.get("data") or []
        if not values:
            return None
        return _clamp(float(values[0].get("value") or 50))
    except Exception as e:
        log.warning("fear_fng_error: %s", e)
        return None


async def fetch_social_sentiment(client: httpx.AsyncClient, query: str | None = None) -> int | None:
    """Crypto social sentiment (0-100) from SearXNG JSON results.

    Scores each result title/description against positive/negative lexicons;
    neutral text counts toward neither side. Returns None when SearXNG is
    unreachable or returns no results.
    """
    try:
        r = await client.get(
            SEARXNG_URL,
            params={"q": query or SEARXNG_QUERY, "format": "json"},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        results = data.get("results") or []
        if not results:
            return None
        texts: list[str] = []
        for res in results:
            if not isinstance(res, dict):
                continue
            title = str(res.get("title") or "")
            content = str(res.get("content") or "")
            if title or content:
                texts.append(f"{title} {content}")
        if not texts:
            return None
        positive = sum(1 for text in texts if _term_hit(text, POSITIVE_TERMS))
        negative = sum(1 for text in texts if _term_hit(text, NEGATIVE_TERMS))
        return _clamp(50 + (positive - negative) / len(texts) * 50)
    except Exception as e:
        log.warning("fear_social_error: %s", e)
        return None


def _onchain_component(raw: Any) -> int | None:
    """Normalize a -100..100 on-chain signal to a 0-100 greed scale."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return _clamp((float(raw) + 100) / 2)


async def fetch_onchain_signals(client: httpx.AsyncClient) -> int | None:
    """On-chain exchange flows / whales / liquidations (0-100 greed scale).

    Reads ``ONCHAIN_SIGNALS_URL`` (a DataBus-style provider) with fields:
    ``exchange_netflow`` (-100..100, positive = inflows = fear),
    ``whale_movements`` (-100..100) and ``liquidations`` (-100..100,
    positive = high liquidations = fear). Returns None when unconfigured.
    """
    if not ONCHAIN_SIGNALS_URL:
        return None
    try:
        r = await client.get(ONCHAIN_SIGNALS_URL, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        netflow = _onchain_component(data.get("exchange_netflow"))
        whales = _onchain_component(data.get("whale_movements"))
        liquidations = _onchain_component(data.get("liquidations"))
        # Inflows and liquidations are fear signals → invert to greed scale.
        if netflow is not None:
            netflow = 100 - netflow
        if liquidations is not None:
            liquidations = 100 - liquidations
        available = [v for v in (netflow, whales, liquidations) if v is not None]
        if not available:
            return None
        return round(sum(available) / len(available))
    except Exception as e:
        log.warning("fear_onchain_error: %s", e)
        return None


def _normalize_sentiment(value: Any) -> float | None:
    """Map a sentiment value to -1..1 (None when unparseable)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("positive", "bullish", "greedy"):
            return 1.0
        if lowered in ("negative", "bearish", "fearful"):
            return -1.0
        if lowered in ("neutral", ""):
            return 0.0
        try:
            value = float(lowered)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        if -1.0 <= value <= 1.0:
            return float(value)
        if 0 <= value <= 100:
            return float(value) / 100.0 * 2.0 - 1.0
        return _clamp(value / 100.0, low=-100, high=100) / 100.0
    return None


def aggregate_news_sentiment(sentiments: list[float]) -> int | None:
    """Average -1..1 sentiment values into a 0-100 greed score."""
    if not sentiments:
        return None
    average = sum(sentiments) / len(sentiments)
    return _clamp((average + 1.0) / 2.0 * 100.0)


async def fetch_news_sentiment(_client: httpx.AsyncClient | None = None) -> int | None:
    """Aggregate sentiment of recently ingested news from Qdrant `news`.

    Reads the ``sentiment`` / ``sentiment_score`` metadata of recent points
    (written by ``news_to_rag.py``). Returns None when Qdrant is
    unreachable or no rated points exist.
    """
    try:
        from rugbrain.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        results, _ = await store.scroll("news", limit=200)
        sentiments: list[float] = []
        for result in results:
            metadata = result.get("metadata") or {}
            normalized = _normalize_sentiment(
                metadata.get("sentiment", metadata.get("sentiment_score"))
            )
            if normalized is not None:
                sentiments.append(normalized)
        return aggregate_news_sentiment(sentiments)
    except Exception as e:
        log.warning("fear_news_sentiment_error: %s", e)
        return None


# ── Composite ───────────────────────────────────────────────────────────


def compute_fear_index(components: dict[str, int | None]) -> int:
    """Weighted 0-100 composite; renormalizes over available components.

    Returns neutral 50 when no component is available.
    """
    available = {
        key: value
        for key, value in components.items()
        if value is not None and not isinstance(value, bool)
    }
    if not available:
        return 50
    total_weight = sum(COMPONENT_WEIGHTS.get(key, 0.0) for key in available)
    if total_weight <= 0:
        return 50
    weighted = sum(COMPONENT_WEIGHTS.get(key, 0.0) * float(available[key]) for key in available)
    return _clamp(weighted / total_weight)


def fear_snapshot(components: dict[str, int | None], now: datetime | None = None) -> dict[str, Any]:
    """Build a full snapshot dict from raw components."""
    value = compute_fear_index(components)
    return {
        "value": value,
        "classification": classify_fear(value),
        "components": {key: (int(v) if v is not None else None) for key, v in components.items()},
        "timestamp": (now or datetime.now(UTC)).isoformat(),
    }


# ── Risk scoring feed ───────────────────────────────────────────────────

_RISK_ORDER: tuple[str, ...] = ("unknown", "low", "medium", "high", "critical")


def _escalate(risk: str, steps: int) -> str:
    index = _RISK_ORDER.index(risk) if risk in _RISK_ORDER else 0
    return _RISK_ORDER[min(index + steps, len(_RISK_ORDER) - 1)]


def adjust_risk_for_market(verdict: dict[str, Any], fear_value: int | None) -> dict[str, Any]:
    """Escalate a token risk verdict when the market is fearful.

    - ``fear_value < 25`` (extreme fear): +1 step for unknown/low/medium.
    - ``fear_value < 40`` (fear): +1 step for low risk.
    - otherwise unchanged.

    Adds ``fear_index`` and ``fear_adjusted`` to the returned copy, and
    appends ``fear_market_risk_boost`` to ``risk_signals`` when escalated.
    """
    out = dict(verdict)
    out["fear_index"] = fear_value
    risk = str(out.get("risk", "unknown"))
    steps = 0
    if fear_value is not None and fear_value < 25:
        steps = 1 if risk in ("unknown", "low", "medium") else 0
    elif fear_value is not None and fear_value < 40:
        steps = 1 if risk == "low" else 0

    if steps:
        out["risk"] = _escalate(risk, steps)
        out["fear_adjusted"] = True
        signals = list(out.get("risk_signals") or [])
        if "fear_market_risk_boost" not in signals:
            signals.append("fear_market_risk_boost")
        out["risk_signals"] = signals
    else:
        out["fear_adjusted"] = False
    return out


# ── ClickHouse persistence (lazy, best-effort) ──────────────────────────


class FearIndexStorage:
    """Best-effort ClickHouse ledger for fear-index snapshots.

    Lazily imports ``clickhouse_connect`` so the module works without the
    package installed; every method degrades to a warning log on failure
    (mirrors ``alerts.ClickHouseAlertLogger``).
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS {db}.{table} (
        ts DateTime64(3, 'UTC'),
        value Float64,
        classification String,
        fng Nullable(Float64),
        social Nullable(Float64),
        onchain Nullable(Float64),
        news Nullable(Float64),
        components String
    ) ENGINE = ReplacingMergeTree()
    ORDER BY (ts)
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._db: str | None = None

    def _get(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            ch = __import__("clickhouse_connect")
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
                            client.command(self.DDL.format(db=db, table=CLICKHOUSE_TABLE))
                            self._db = db
                            break
                        except Exception as e:
                            last_err = e
                            client = None
                    if self._db is not None:
                        break
                except Exception as e:
                    last_err = e
                    client = None
            if self._db is None:
                raise RuntimeError(f"clickhouse connect failed: {last_err}")
            self._client = client
            log.info("fear_index_clickhouse_ready table=%s db=%s", CLICKHOUSE_TABLE, self._db)
        except Exception as e:
            log.warning("fear_index_clickhouse_unavailable: %s", e)
            self._client = False
        return self._client if self._client is not False else None

    def insert(self, snapshot: dict[str, Any]) -> bool:
        """Persist one snapshot row; False on any failure."""
        client = self._get()
        if client is None or self._db is None:
            return False
        try:
            components = snapshot.get("components") or {}
            client.insert(
                f"{self._db}.{CLICKHOUSE_TABLE}",
                [
                    [
                        datetime.now(UTC),
                        float(snapshot.get("value", 50)),
                        str(snapshot.get("classification", "neutral")),
                        components.get("fng"),
                        components.get("social"),
                        components.get("onchain"),
                        components.get("news"),
                        json.dumps(components, default=str)[:5000],
                    ]
                ],
                column_names=[
                    "ts",
                    "value",
                    "classification",
                    "fng",
                    "social",
                    "onchain",
                    "news",
                    "components",
                ],
            )
            return True
        except Exception as e:
            log.warning("fear_index_clickhouse_insert_failed: %s", e)
            return False

    def query_history(self, days: int) -> list[dict[str, Any]]:
        """Recent (ts, value, classification) rows; [] on failure."""
        client = self._get()
        if client is None or self._db is None:
            return []
        try:
            # Table/db come from trusted env config (never user input); the
            # only dynamic predicate value is bound via parameters.
            rows = client.query(
                f"SELECT ts, value, classification FROM {self._db}.{CLICKHOUSE_TABLE} "  # noqa: S608  # nosec B608
                "WHERE ts >= now() - INTERVAL {days:UInt16} DAY ORDER BY ts ASC",
                parameters={"days": max(1, int(days))},
            )
            result: list[dict[str, Any]] = []
            for row in rows.result_rows:
                ts_value = row[0]
                result.append(
                    {
                        "ts": (
                            ts_value.isoformat()
                            if hasattr(ts_value, "isoformat")
                            else str(ts_value)
                        ),
                        "value": float(row[1]),
                        "classification": str(row[2]),
                    }
                )
            return result
        except Exception as e:
            log.warning("fear_index_clickhouse_query_failed: %s", e)
            return []


_storage = FearIndexStorage()

# ── In-memory history ring ──────────────────────────────────────────────

_history: deque[dict[str, Any]] = deque(maxlen=10_000)
_last_snapshot: dict[str, Any] | None = None


def _record(snapshot: dict[str, Any]) -> None:
    """Append a snapshot to the in-process history ring."""
    global _last_snapshot
    _history.append(
        {
            "ts": snapshot.get("timestamp"),
            "value": snapshot.get("value"),
            "classification": snapshot.get("classification"),
        }
    )
    _last_snapshot = snapshot


def _window_seconds(window: str) -> int:
    return _WINDOW_SECONDS.get(str(window).lower(), 86400)


async def _samples_for_window(window: str) -> list[dict[str, Any]]:
    """Window-filtered samples from memory, merged with ClickHouse rows."""
    seconds = _window_seconds(window)
    cutoff = datetime.now(UTC) - timedelta(seconds=seconds)
    seen: dict[str, dict[str, Any]] = {}
    for entry in _history:
        ts = _parse_dt(entry.get("ts"))
        if ts is None or ts < cutoff:
            continue
        seen[ts.isoformat()] = entry
    try:
        rows = _storage.query_history(max(1, round(seconds / 86400)))
        for row in rows:
            ts = _parse_dt(row.get("ts"))
            if ts is None or ts < cutoff:
                continue
            seen[ts.isoformat()] = row
    except Exception as exc:
        log.debug("fear_index_window_clickhouse_merge_failed: %s", exc)
    return [seen[key] for key in sorted(seen)]


# ── Redis cache ─────────────────────────────────────────────────────────


async def _redis_get() -> str | None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        try:
            value = await r.get(CACHE_KEY)
            return str(value) if value is not None else None
        finally:
            await r.aclose()
    except Exception:
        return None


async def _cache_get() -> dict[str, Any] | None:
    cached = await _redis_get()
    if not cached:
        return None
    try:
        loaded: dict[str, Any] = json.loads(cached)
        return loaded
    except json.JSONDecodeError:
        return None


async def _cache_set(snapshot: dict[str, Any]) -> None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        try:
            await r.set(CACHE_KEY, json.dumps(snapshot), ex=CACHE_TTL)
        finally:
            await r.aclose()
    except Exception as e:
        log.debug("fear_index_cache_write_failed: %s", e)


# ── Polling ─────────────────────────────────────────────────────────────


async def poll_fear_index(use_cache: bool = True) -> dict[str, Any]:
    """Fetch all components, compute, record, persist and cache a snapshot."""
    if use_cache:
        cached = await _cache_get()
        if cached:
            return cached
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        results = await asyncio.gather(
            fetch_fng(client),
            fetch_social_sentiment(client),
            fetch_onchain_signals(client),
            fetch_news_sentiment(client),
            return_exceptions=True,
        )
    components: dict[str, int | None] = {
        "fng": _as_int_or_none(results[0]),
        "social": _as_int_or_none(results[1]),
        "onchain": _as_int_or_none(results[2]),
        "news": _as_int_or_none(results[3]),
    }
    snapshot = fear_snapshot(components)
    _record(snapshot)
    _storage.insert(snapshot)
    await _cache_set(snapshot)
    return snapshot


_poller_task: asyncio.Task[Any] | None = None
_poller_lock = asyncio.Lock()


async def _poller_loop() -> None:
    while True:
        try:
            await poll_fear_index()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("fear_index_poll_failed")
        await asyncio.sleep(FEAR_INDEX_POLL_SECONDS)


async def start_fear_index_poller() -> bool:
    """Start the background poller (idempotent)."""
    global _poller_task
    async with _poller_lock:
        if _poller_task is not None and not _poller_task.done():
            return True
        _poller_task = asyncio.create_task(_poller_loop(), name="fear-index-poller")
        return True


async def stop_fear_index_poller() -> None:
    """Cancel the background poller (idempotent)."""
    global _poller_task
    task = _poller_task
    _poller_task = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def is_fear_index_poller_running() -> bool:
    """True when the poller task is active."""
    return _poller_task is not None and not _poller_task.done()


# ── API-facing helpers ──────────────────────────────────────────────────


async def get_fear_index(window: str = "24h", force: bool = False) -> dict[str, Any]:
    """Return the current snapshot + window samples (fail-open).

    Resolution order: Redis cache → in-process memory → live poll.
    """
    source = "cache"
    snapshot: dict[str, Any] | None = None
    if not force:
        snapshot = await _cache_get()
        if snapshot is None:
            snapshot = _last_snapshot
            source = "memory"
    if snapshot is None:
        snapshot = await poll_fear_index(use_cache=False)
        source = "fresh"
    samples = await _samples_for_window(window)
    return {
        **snapshot,
        "window": window,
        "samples": samples,
        "source": source,
    }


async def fear_index_history(days: int = 30) -> dict[str, Any]:
    """Historical chart points, ClickHouse-first with memory fallback."""
    try:
        rows = _storage.query_history(days)
        if rows:
            return {"days": days, "points": rows, "source": "clickhouse"}
    except Exception as exc:
        log.debug("fear_index_history_clickhouse_failed: %s", exc)
    if _history:
        points = [
            {"ts": entry["ts"], "value": entry["value"], "classification": entry["classification"]}
            for entry in _history
        ]
        return {"days": days, "points": points, "source": "memory"}
    return {"days": days, "points": [], "source": "empty"}


async def current_fear_value() -> int | None:
    """Current fear index value (0-100), None when unavailable."""
    snapshot = await get_fear_index()
    value = snapshot.get("value")
    return int(value) if isinstance(value, (int, float)) else None


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
