"""SQL construction for the OHLCV price series. One job: build the strings.

Split out of :mod:`rugbrain.ta.reader` so the SQL text and the thing that
executes it are separate modules. The reader decides *when* a read happens and
what to do when it fails; this module decides *what text is sent*.

Every caller-supplied value passes through :func:`lit`, which escapes quotes and
backslashes, so the f-strings below interpolate only literals and config values —
never raw input. The ``noqa: S608`` markers record that reasoning at each site
rather than silencing the rule module-wide.
"""

from __future__ import annotations

import os
from typing import Final

#: Table config. Falls back to wallet_memory because that is where the
#: dex_ohlcv_hourly table lives; the rmi database has no price series.
DATABASE: Final[str] = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")
TABLE: Final[str] = "dex_ohlcv_hourly"

#: Guard rail: a window is expressed in hours; 90d of hourly bars is 2160 rows.
MAX_WINDOW_HOURS: Final[int] = 24 * 365


def lit(value: str) -> str:
    """Quote a ClickHouse string literal, escaping quotes and backslashes."""
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def candles_sql(*, chain: str, pair_address: str, start: str, end: str) -> str:
    """Ascending OHLCV rows for one pair inside ``[start, end]``.

    ``start``/``end`` are pre-formatted timestamps; ``chain``/``pair_address``
    are raw values and are quoted here.
    """
    # ruff flags the interpolated f-strings below; every caller value goes
    # through lit(), so the only interpolated pieces are quoted literals and
    # module config constants.
    return (
        "SELECT timestamp, open, high, low, close, volume, trades "  # noqa: S608
        f"FROM {DATABASE}.{TABLE} "
        "WHERE chain = {chain} AND pair_address = {pair} "
        "AND timestamp >= {start} AND timestamp <= {end} "
        "ORDER BY timestamp ASC"
    ).format(
        chain=lit(chain),
        pair=lit(pair_address),
        start=lit(start),
        end=lit(end),
    )


def stats_sql() -> str:
    """Row count, distinct pair count and freshness for the whole table."""
    # No caller input at all: only the two module config constants.
    return (
        f"SELECT count() AS rows, uniqExact(pair_address) AS pairs, "  # noqa: S608
        f"max(timestamp) AS fresh FROM {DATABASE}.{TABLE}"
    )


def top_pairs_sql(*, days: int, limit: int) -> str:
    """Pairs ordered by how densely they were traded over the last *days*.

    ``days`` and ``limit`` are forced through ``int()`` — they are coerced here
    rather than interpolated raw, so a non-numeric argument fails loudly at the
    boundary instead of producing a syntax error inside ClickHouse.
    """
    n_days, n_limit = int(days), int(limit)
    # n_days/n_limit were coerced with int() above, so no string can reach here.
    return (
        "SELECT chain, pair_address, count() AS c FROM "  # noqa: S608
        f"{DATABASE}.{TABLE} "
        f"WHERE timestamp >= now() - INTERVAL {n_days} DAY "
        f"GROUP BY chain, pair_address ORDER BY c DESC LIMIT {n_limit}"
    )


__all__ = [
    "DATABASE",
    "MAX_WINDOW_HOURS",
    "TABLE",
    "candles_sql",
    "lit",
    "stats_sql",
    "top_pairs_sql",
]
