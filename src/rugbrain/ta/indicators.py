"""Technical indicators as pure functions over ``list[float]``.

No pandas, no numpy: every indicator takes a plain sequence of floats and
returns a list of the same length, padded with ``None`` while the window is
still filling. That length-preserving contract is what lets an indicator be
zipped back onto its candle series without an off-by-one, and it is why the
backtest can never silently misalign a value with a bar.

**Causality.** The value at index ``i`` is a function of ``series[:i+1]`` and of
``i`` alone. No indicator here ever indexes a negative offset, and none of them
mutates its input. ``tests/test_ta_indicators.py`` checks the arithmetic against
hand-computed values; ``tests/test_ta_no_lookahead.py`` checks the causality.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Number = float
Series = list[float | None]


def sma(values: Sequence[Number], period: int) -> Series:
    """Simple moving average. ``None`` for the first ``period-1`` positions."""
    _check_period(period)
    n = len(values)
    out: Series = [None] * n
    if n < period:
        return out
    running = math.fsum(values[:period])
    out[period - 1] = running / period
    for i in range(period, n):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def ema(values: Sequence[Number], period: int) -> Series:
    """Exponential moving average, seeded with the SMA of the first *period*."""
    _check_period(period)
    n = len(values)
    out: Series = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = math.fsum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[Number], period: int = 14) -> Series:
    """Wilder's RSI on closes. First value lands at index *period*."""
    _check_period(period)
    n = len(values)
    out: Series = [None] * n
    if n <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, n):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def true_range(highs: Sequence[Number], lows: Sequence[Number], closes: Sequence[Number]) -> Series:
    """Wilder true range. ``out[0]`` is ``high[0]-low[0]`` (no prior close)."""
    _same_len(highs, lows, closes)
    out: Series = [None] * len(closes)
    if not closes:
        return out
    out[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        prev_close = closes[i - 1]
        out[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        )
    return out


def atr(
    highs: Sequence[Number], lows: Sequence[Number], closes: Sequence[Number], period: int = 14
) -> Series:
    """Wilder-smoothed ATR of the true range."""
    _check_period(period)
    tr = true_range(highs, lows, closes)
    n = len(tr)
    out: Series = [None] * n
    if n < period:
        return out
    first = [v for v in tr[:period] if v is not None]
    if len(first) != period:
        return out
    prev = math.fsum(first) / period
    out[period - 1] = prev
    for i in range(period, n):
        value = tr[i]
        assert value is not None  # tr is fully populated from index 0
        prev = (prev * (period - 1) + value) / period
        out[i] = prev
    return out


def volume_delta(volumes: Sequence[Number], period: int = 20) -> Series:
    """Volume minus its own SMA — positive means above-average flow."""
    _check_period(period)
    out: Series = [None] * len(volumes)
    baseline = sma(volumes, period)
    for i, v in enumerate(volumes):
        base = baseline[i]
        out[i] = None if base is None else v - base
    return out


def volatility(closes: Sequence[Number], period: int = 20, periods_per_year: int = 8760) -> Series:
    """Annualised stdev of log returns (hourly candles by default)."""
    _check_period(period)
    n = len(closes)
    out: Series = [None] * n
    if n <= period:
        return out
    logrets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n) if closes[i - 1] > 0]
    scale = math.sqrt(periods_per_year)
    for i in range(period, n):
        window = logrets[i - period : i]
        if len(window) != period:
            continue
        out[i] = _stdev(window) * scale
    return out


def drawdown(closes: Sequence[Number]) -> Series:
    """Running drawdown from the running peak, as a negative fraction (0.0 = at peak)."""
    out: Series = []
    peak = None
    for c in closes:
        peak = c if peak is None or c > peak else peak
        out.append(0.0 if peak == 0 else (c - peak) / peak)
    return out


def max_drawdown(closes: Sequence[Number]) -> float:
    """The single worst peak-to-trough decline in the series (0.0 or negative)."""
    return min(drawdown(closes), default=0.0)


def _stdev(window: Sequence[Number]) -> float:
    mean = math.fsum(window) / len(window)
    var = math.fsum((x - mean) ** 2 for x in window) / (len(window) - 1)
    return math.sqrt(max(var, 0.0))


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _check_period(period: int) -> None:
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")


def _same_len(*seqs: Sequence[Number]) -> None:
    if len({len(s) for s in seqs}) > 1:
        raise ValueError("ohlc series must be the same length")


__all__ = [
    "atr",
    "drawdown",
    "ema",
    "max_drawdown",
    "rsi",
    "sma",
    "true_range",
    "volatility",
    "volume_delta",
]
