"""The ``Candle`` value type and the point-in-time series it lives in.

Deliberately dependency-free (stdlib only). Indicators take plain
``list[float]``; candles carry the identity fields (``pair``, ``chain``,
``timestamp``) that a numeric series throws away, and the ordering invariant
everything else relies on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

#: Canonical field order of ``wallet_memory.dex_ohlcv_hourly``.
CANDLE_FIELDS = (
    "chain",
    "pair_address",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trades",
)


@dataclass(frozen=True, slots=True)
class Candle:
    """One hourly OHLCV bar.

    ``timestamp`` is the candle's OPEN time in UTC (ClickHouse stores the hour
    bucket), so a signal stamped at ``t`` knows it has seen bars ``<= t`` and no
    bar at ``t + 1h``.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int = 0
    chain: str = ""
    pair_address: str = ""

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def typical_price(self) -> float:
        """(H+L+C)/3 — the standard smoothing input for RSI and friends."""
        return (self.high + self.low + self.close) / 3.0

    def as_row(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trades": self.trades,
            "chain": self.chain,
            "pair_address": self.pair_address,
        }


class Series:
    """An immutable, ascending-by-time candle series with a point-in-time view.

    The only way to read "what did we know at time T" is :meth:`upto`. Nothing in
    this package indexes with a future offset, and ``upto`` is what the backtest
    harness uses on every bar, so a lookahead bug has to be a deliberate one.
    """

    __slots__ = ("_candles",)

    def __init__(self, candles: Iterable[Candle]) -> None:
        self._candles: tuple[Candle, ...] = tuple(candles)

    def __len__(self) -> int:
        return len(self._candles)

    def __iter__(self):
        return iter(self._candles)

    def __getitem__(self, idx: int) -> Candle:
        return self._candles[idx]

    def __repr__(self) -> str:  # pragma: no cover - formatting only
        if not self._candles:
            return "Series([])"
        return (
            f"Series({len(self._candles)} candles, "
            f"{self._candles[0].timestamp.isoformat()}..{self._candles[-1].timestamp.isoformat()})"
        )

    @property
    def candles(self) -> tuple[Candle, ...]:
        return self._candles

    @property
    def last(self) -> Candle:
        if not self._candles:
            raise IndexError("empty series")
        return self._candles[-1]

    def sorted_by_time(self) -> Series:
        """Return an ascending-time copy. Unsorted input is a data defect, not a crash."""
        return Series(sorted(self._candles, key=lambda c: c.timestamp))

    def upto(self, ts: datetime) -> Series:
        """The prefix visible at *ts* — every candle whose OPEN time is <= ts.

        This is the no-lookahead primitive: ``len(s.upto(t)) == i + 1`` for the
        candle at index ``i``, always.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return Series(c for c in self._candles if c.timestamp <= ts)

    def upto_index(self, i: int) -> Series:
        """The prefix ending at (and including) index *i*."""
        if i < 0 or i >= len(self._candles):
            raise IndexError(f"index {i} out of range for {len(self._candles)} candles")
        return Series(self._candles[: i + 1])

    def closes(self) -> list[float]:
        return [c.close for c in self._candles]

    def highs(self) -> list[float]:
        return [c.high for c in self._candles]

    def lows(self) -> list[float]:
        return [c.low for c in self._candles]

    def volumes(self) -> list[float]:
        return [c.volume for c in self._candles]

    def typical_prices(self) -> list[float]:
        return [c.typical_price for c in self._candles]

    def timestamps(self) -> list[datetime]:
        return [c.timestamp for c in self._candles]


def closes_of(candles: Sequence[Candle]) -> list[float]:
    """Close-price series from any candle sequence (lists, tuples, generators)."""
    return [c.close for c in candles]


__all__ = ["CANDLE_FIELDS", "Candle", "Series", "closes_of"]
