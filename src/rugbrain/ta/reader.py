"""Read hourly OHLCV candles out of ``wallet_memory.dex_ohlcv_hourly``.

Reuses the fleet's existing ClickHouse access pattern — ``clickhouse_connect``
over the HTTP interface, lazily imported so the module imports on a host without
it (the same convention as :mod:`rugbrain.alerts.clickhouse` and
:mod:`rugbrain.system_health`). No new client library.

Read-only by construction: this module issues exactly one ``SELECT`` and has no
code path that can emit a mutation.

The table is the price-series substrate the mission eval was missing —
2,596,363 hourly candles over 53,935 DEX pairs, fresh to the current hour, with
columns ``chain, pair_address, timestamp, open, high, low, close, volume, trades``.

One job: connect, execute, and turn rows into :class:`Candle` objects. The SQL
text itself lives in :mod:`rugbrain.ta.ohlcv_sql`.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .candles import Candle, Series
from .ohlcv_sql import MAX_WINDOW_HOURS, candles_sql, stats_sql, top_pairs_sql

log = logging.getLogger("rugbrain.ta")

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rugbrain")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")

#: Re-exported names other modules and the public API rely on.
DATABASE = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")
OHLCV_TABLE = "dex_ohlcv_hourly"
DEFAULT_TIMEOUT_S = 30.0

_TS_FMT = "%Y-%m-%d %H:%M:%S"


class CandleReadError(RuntimeError):
    """Raised when the OHLCV read fails. Never swallowed into synthetic data."""


@dataclass(frozen=True, slots=True)
class PairRef:
    """Identifies one DEX pair (chain + pool address)."""

    chain: str
    pair_address: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.chain}:{self.pair_address}"


def _client() -> Any:
    """Lazily build a clickhouse_connect client, or raise CandleReadError."""
    try:
        ch = importlib.import_module("clickhouse_connect")
    except ImportError as exc:  # pragma: no cover - depends on host
        raise CandleReadError("clickhouse_connect is not installed on this host") from exc
    attempts = [CLICKHOUSE_PORT]
    if CLICKHOUSE_PORT != 8123:
        attempts.append(8123)
    last: Exception | None = None
    for port in attempts:
        try:
            return ch.get_client(
                host=CLICKHOUSE_HOST,
                port=port,
                interface=CLICKHOUSE_INTERFACE,
                username=CLICKHOUSE_USER,
                password=CLICKHOUSE_PASSWORD,
                database=DATABASE,
            )
        except Exception as exc:  # a port may be down; try the next
            last = exc
    raise CandleReadError(f"clickhouse unreachable on {attempts}: {type(last).__name__}: {last}")


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value)).replace(tzinfo=UTC)


def _row_to_candle(row: Sequence[Any], ref: PairRef) -> Candle:
    return Candle(
        timestamp=_as_utc(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        trades=int(row[6]),
        chain=ref.chain,
        pair_address=ref.pair_address,
    )


def _rows(client: Any | None, sql: str, what: str) -> list[Sequence[Any]]:
    """Run *sql* and return its rows, converting any failure to CandleReadError."""
    ch = client if client is not None else _client()
    try:
        return ch.query(sql).result_rows
    except Exception as exc:
        raise CandleReadError(f"{what} failed: {type(exc).__name__}: {exc}") from exc


def fetch_candles(
    ref: PairRef,
    hours: int = 168,
    *,
    end: datetime | None = None,
    client: Any | None = None,
) -> Series:
    """Fetch the last *hours* hourly candles for *ref*, ascending by time.

    ``end`` defaults to now and is applied as an INCLUSIVE upper bound on the
    candle's open time. The window is clamped to ``MAX_WINDOW_HOURS`` so a typo
    cannot ask ClickHouse for the whole 2.6M-row table.
    """
    if hours <= 0:
        raise ValueError("hours must be positive")
    if hours > MAX_WINDOW_HOURS:
        raise ValueError(f"hours {hours} exceeds MAX_WINDOW_HOURS={MAX_WINDOW_HOURS}")
    end_ts = end or datetime.now(UTC)
    if end_ts.tzinfo is None:
        end_ts = end_ts.replace(tzinfo=UTC)
    start_ts = end_ts - timedelta(hours=hours)

    sql = candles_sql(
        chain=ref.chain,
        pair_address=ref.pair_address,
        start=start_ts.strftime(_TS_FMT),
        end=end_ts.strftime(_TS_FMT),
    )
    rows = _rows(client, sql, f"ohlcv query for {ref}")
    log.info("ta_fetch_candles pair=%s rows=%d window_hours=%d", ref, len(rows), hours)
    return Series(_row_to_candle(r, ref) for r in rows)


def fetch_candles_upto(
    ref: PairRef, hours: int = 168, *, end: datetime | None = None, client: Any | None = None
) -> Series:
    """Alias kept explicit: fetch candles strictly bounded by an as-of time."""
    return fetch_candles(ref, hours, end=end, client=client)


def table_stats(client: Any | None = None) -> dict[str, int]:
    """Row count, distinct pair count and freshness of the OHLCV table."""
    row = _rows(client, stats_sql(), "ohlcv stats")[0]
    return {"rows": int(row[0]), "pairs": int(row[1]), "fresh": str(row[2])}


def top_pairs(days: int = 30, limit: int = 10, client: Any | None = None) -> list[PairRef]:
    """The pairs with the densest recent coverage — a sane default for demos."""
    rows = _rows(client, top_pairs_sql(days=days, limit=limit), "top_pairs")
    return [PairRef(str(r[0]), str(r[1])) for r in rows]


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "MAX_WINDOW_HOURS",
    "OHLCV_TABLE",
    "CandleReadError",
    "PairRef",
    "fetch_candles",
    "fetch_candles_upto",
    "table_stats",
    "top_pairs",
]
