"""Candle/Series semantics and the reader's SQL construction.

The reader tests use a fake client that records the SQL it was handed — no live
ClickHouse. The point is that the reader emits exactly one SELECT, bound to the
right table and window, and that it fails loudly rather than returning
synthetic data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.ta.candles import Candle, Series, closes_of
from rugbrain.ta.reader import (
    MAX_WINDOW_HOURS,
    CandleReadError,
    PairRef,
    fetch_candles,
    table_stats,
    top_pairs,
)

START = datetime(2026, 9, 1, tzinfo=UTC)


def _series(n: int = 5, *, shuffled: bool = False) -> Series:
    candles = [
        Candle(
            timestamp=START + timedelta(hours=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0 + i,
            trades=3 + i,
            chain="ethereum",
            pair_address="0xabc",
        )
        for i in range(n)
    ]
    return Series(candles[::-1] if shuffled else candles)


# ── Candle ────────────────────────────────────────────────────────────────


def test_candle_direction_and_range() -> None:
    c = Candle(START, open=10.0, high=12.0, low=9.0, close=11.0, volume=1.0)
    assert c.is_bullish
    assert c.range == 3.0
    assert c.typical_price == pytest.approx((12.0 + 9.0 + 11.0) / 3)


def test_bearish_candle_is_not_bullish() -> None:
    assert not Candle(START, open=12.0, high=13.0, low=9.0, close=10.0, volume=1.0).is_bullish


def test_candle_as_row_round_trips_the_identity_fields() -> None:
    row = _series(1).last.as_row()
    assert row["chain"] == "ethereum"
    assert row["pair_address"] == "0xabc"
    assert row["close"] == 100.5


# ── Series ────────────────────────────────────────────────────────────────


def test_series_is_iterable_and_sized() -> None:
    s = _series(4)
    assert len(s) == 4
    assert [c.close for c in s] == [100.5, 101.5, 102.5, 103.5]
    assert s.last.close == 103.5


def test_empty_series_raises_on_last() -> None:
    with pytest.raises(IndexError):
        _ = Series([]).last


def test_sorted_by_time_fixes_an_out_of_order_feed() -> None:
    s = _series(4, shuffled=True)
    assert s[0].timestamp > s[-1].timestamp, "input was deliberately reversed"
    ordered = s.sorted_by_time()
    stamps = [c.timestamp for c in ordered]
    assert stamps == sorted(stamps)
    assert [c.close for c in ordered] == [100.5, 101.5, 102.5, 103.5]


def test_upto_is_inclusive_of_the_boundary_bar() -> None:
    s = _series(5)
    prefix = s.upto(s[2].timestamp)
    assert len(prefix) == 3
    assert prefix.last.timestamp == s[2].timestamp


def test_upto_treats_a_naive_datetime_as_utc() -> None:
    s = _series(5)
    naive = s[2].timestamp.replace(tzinfo=None)
    assert len(s.upto(naive)) == 3


def test_upto_before_the_first_bar_is_empty() -> None:
    s = _series(5)
    assert len(s.upto(START - timedelta(hours=1))) == 0


def test_upto_index_rejects_out_of_range() -> None:
    s = _series(5)
    with pytest.raises(IndexError):
        s.upto_index(5)
    with pytest.raises(IndexError):
        s.upto_index(-1)


def test_column_accessors_align_with_the_candles() -> None:
    s = _series(3)
    assert s.closes() == [c.close for c in s]
    assert s.highs() == [c.high for c in s]
    assert s.lows() == [c.low for c in s]
    assert s.volumes() == [c.volume for c in s]
    assert len(s.timestamps()) == 3
    assert s.typical_prices()[0] == s[0].typical_price


def test_closes_of_accepts_any_sequence() -> None:
    assert closes_of(_series(3)) == [100.5, 101.5, 102.5]
    assert closes_of([]) == []


# ── reader: SQL construction, no live ClickHouse ──────────────────────────


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self.result_rows = rows


class _FakeClient:
    """Records SQL and returns canned rows. Fails the test on any mutation."""

    def __init__(self, rows: list[tuple] | None = None, *, error: Exception | None = None) -> None:
        self.queries: list[str] = []
        self.rows = rows or []
        self.error = error

    def query(self, sql: str) -> _FakeResult:
        self.queries.append(sql)
        if self.error is not None:
            raise self.error
        return _FakeResult(self.rows)


def _ohlcv_rows(n: int = 3) -> list[tuple]:
    return [(datetime(2026, 9, 27, h, tzinfo=UTC), 1.0, 2.0, 0.5, 1.5, 100.0, 7) for h in range(n)]


def test_fetch_emits_a_single_select_against_the_ohlcv_table() -> None:
    client = _FakeClient(_ohlcv_rows())
    ref = PairRef("ethereum", "0xdead")
    series = fetch_candles(ref, hours=24, end=datetime(2026, 9, 28, tzinfo=UTC), client=client)

    assert len(series) == 3
    assert len(client.queries) == 1
    sql = client.queries[0]
    assert sql.lstrip().upper().startswith("SELECT")
    assert "dex_ohlcv_hourly" in sql
    assert "ORDER BY timestamp ASC" in sql
    for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER"):
        assert forbidden not in sql.upper(), "the reader must be read-only by construction"


def test_fetch_maps_rows_onto_candles_with_identity_fields() -> None:
    client = _FakeClient(_ohlcv_rows(1))
    ref = PairRef("base", "0xfeed")
    candle = fetch_candles(ref, hours=1, end=datetime(2026, 9, 28, tzinfo=UTC), client=client).last
    assert candle.chain == "base"
    assert candle.pair_address == "0xfeed"
    assert (candle.open, candle.high, candle.low, candle.close) == (1.0, 2.0, 0.5, 1.5)
    assert candle.volume == 100.0
    assert candle.trades == 7


def test_fetch_escapes_quotes_in_identifiers() -> None:
    client = _FakeClient([])
    fetch_candles(
        PairRef("e", "0x' OR 1=1 --"), hours=1, end=datetime(2026, 9, 28, tzinfo=UTC), client=client
    )
    assert "\\'" in client.queries[0], "an unescaped quote would break out of the literal"


def test_fetch_rejects_a_non_positive_window() -> None:
    with pytest.raises(ValueError):
        fetch_candles(PairRef("e", "0x"), hours=0, client=_FakeClient([]))


def test_fetch_rejects_a_window_larger_than_the_cap() -> None:
    with pytest.raises(ValueError):
        fetch_candles(PairRef("e", "0x"), hours=MAX_WINDOW_HOURS + 1, client=_FakeClient([]))


def test_fetch_raises_rather_than_returning_synthetic_data() -> None:
    """A ClickHouse failure must be loud — never a plausible-looking fallback."""
    client = _FakeClient(error=RuntimeError("connection refused"))
    with pytest.raises(CandleReadError, match="connection refused"):
        fetch_candles(PairRef("e", "0x"), hours=1, client=client)


def test_table_stats_reads_rows_pairs_and_freshness() -> None:
    client = _FakeClient([(2596363, 53935, "2026-09-28 17:00:00")])
    stats = table_stats(client)
    assert stats["rows"] == 2596363
    assert stats["pairs"] == 53935
    assert stats["fresh"] == "2026-09-28 17:00:00"


def test_table_stats_raises_on_failure() -> None:
    with pytest.raises(CandleReadError):
        table_stats(_FakeClient(error=RuntimeError("boom")))


def test_top_pairs_returns_pair_refs() -> None:
    client = _FakeClient([("gnosis", "0xaaa", 690), ("ethereum", "0xbbb", 686)])
    pairs = top_pairs(days=30, limit=2, client=client)
    assert pairs == [PairRef("gnosis", "0xaaa"), PairRef("ethereum", "0xbbb")]
    assert str(pairs[0]) == "gnosis:0xaaa"
    assert "INTERVAL 30 DAY" in client.queries[0]


def test_pair_ref_str_is_readable() -> None:
    assert str(PairRef("ethereum", "0x1")) == "ethereum:0x1"
