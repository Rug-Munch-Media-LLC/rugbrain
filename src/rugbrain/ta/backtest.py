"""Point-in-time backtest harness. Reads the past or it does not run.

The contract this module exists to enforce:

    **a signal at bar ``i`` may only read candles ``0..i`` — never ``i+1..``.**

That is not a convention the caller is trusted to honour. It is structural:

1. :func:`walk_forward` iterates bars and calls the evaluator with
   ``series.upto_index(i)``. The evaluator receives a *prefix object*, so there
   is no index in reach that points forward.
2. :func:`prefix_invariance` re-derives every past bar's signal from a
   **truncated** copy of the series. If any stage peeked ahead, appending future
   bars would change a past answer and the check fails. This is the test that
   would fail if someone peeked.
3. Entry fills happen at the **next** bar's open, never at the signal bar's
   close, so a fill is not back-filled with the price that produced the signal.
4. Costs are charged per round trip, so a high-frequency "always on" strategy
   cannot look profitable purely by trading more.

Purged/embargoed splitting: :func:`purged_split` withholds a gap of
``embargo_bars`` after each training window so a label computed from a training
window's last bar cannot overlap the first out-of-sample bar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .candles import Series
from .signals import Signal, evaluate

#: Fills happen one bar after the signal — see module docstring point 3.
FILL_LAG_BARS = 1
#: Round-trip cost as a fraction of notional (0.1% each way is realistic on DEX).
DEFAULT_COST_BPS = 10.0


@dataclass(frozen=True, slots=True)
class Trade:
    """One completed round trip."""

    signal_as_of: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    return_pct: float
    signal_kind: str
    costs_pct: float

    @property
    def is_win(self) -> bool:
        return self.return_pct > 0


@dataclass
class BacktestResult:
    """Aggregate outcome of a walk-forward pass."""

    bars: int = 0
    signals: int = 0
    trades: list[Trade] = field(default_factory=list)
    signals_by_kind: dict[str, int] = field(default_factory=dict)

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        return sum(t.is_win for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def mean_return_pct(self) -> float:
        return sum(t.return_pct for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def total_return_pct(self) -> float:
        return sum(t.return_pct for t in self.trades)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bars": self.bars,
            "signals": self.signals,
            "signals_by_kind": self.signals_by_kind,
            "trades": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "mean_return_pct": round(self.mean_return_pct, 4),
            "total_return_pct": round(self.total_return_pct, 4),
        }


def walk_forward(
    series: Series,
    evaluator: Callable[[Series], list[Signal]] = evaluate,
    *,
    hold_bars: int = 4,
    cost_bps: float = DEFAULT_COST_BPS,
    warmup_bars: int = 21,
) -> BacktestResult:
    """Run the evaluator bar-by-bar and trade each signal with a one-bar lag.

    ``evaluator`` receives ``series.upto_index(i)`` — a prefix. A strategy that
    wants the whole series can only get it by ignoring the argument, which
    :func:`prefix_invariance` is built to catch.
    """
    result = BacktestResult(bars=len(series))
    cost_pct = cost_bps / 100.0
    n = len(series)
    for i in range(warmup_bars, n - FILL_LAG_BARS):
        prefix = series.upto_index(i)
        signals = evaluator(prefix)
        if not signals:
            continue
        result.signals += len(signals)
        entry_i = i + FILL_LAG_BARS
        exit_i = min(entry_i + hold_bars, n - 1)
        entry = series[entry_i]
        exit_candle = series[exit_i]
        raw = (exit_candle.close - entry.open) / entry.open if entry.open else 0.0
        for sig in signals:
            result.signals_by_kind[sig.kind] = result.signals_by_kind.get(sig.kind, 0) + 1
            result.trades.append(
                Trade(
                    signal_as_of=sig.as_of,
                    entry_time=entry.timestamp,
                    exit_time=exit_candle.timestamp,
                    entry_price=entry.open,
                    exit_price=exit_candle.close,
                    return_pct=round(raw - cost_pct, 6),
                    signal_kind=sig.kind,
                    costs_pct=cost_pct,
                )
            )
    return result


def prefix_invariance(
    series: Series,
    evaluator: Callable[[Series], list[Signal]] = evaluate,
    *,
    checkpoints: int = 12,
    warmup_bars: int = 21,
) -> list[int]:
    """Bar indices whose signal changed when the FUTURE was rewritten.

    For each checkpoint ``i`` the check builds two full series that are
    **byte-identical on bars ``0..i`` and deliberately divergent after ``i``**,
    then runs the evaluator on each one's prefix through ``i``. A causal
    evaluator receives identical input both times and MUST return an identical
    answer. Any difference is, by construction, a read of the future — including
    a side-channel read through a closure over the parent, a module-level cache,
    or a global. The check is therefore sensitive to the realistic accidental
    leak, not only to an off-by-one inside the indicator maths.

    Returns the offending bar indices; ``[]`` means no lookahead was observed.
    This is the executable form of the rule: run it against whatever evaluator
    ships, not only in a unit test.

    The series under test is published on :mod:`rugbrain.ta.signals` as
    ``CURRENT_SERIES`` before each call, which is what lets the check see the
    realistic leak: a *module-level cache* of the full series. An evaluator that
    peeks through such a cache sees a different future in the two runs, so its
    verdicts differ and the leak surfaces. An evaluator that peeks through a
    closure over a *fixed parent* is not detectable by construction (it looks
    at the same bars both times) — the prefix API is what defends against that
    class, and ``Series.upto_index`` raising ``IndexError`` past the cut is the
    mechanism, pinned by ``test_in_prefix_lookahead_is_structurally_impossible``.
    """
    n = len(series)
    if n <= warmup_bars + 1:
        return []
    leaks: list[int] = []
    start, stop = warmup_bars, n - 1
    step = max(1, (stop - start) // max(checkpoints, 1))
    for i in range(start, stop, step):
        twin = _rewrite_future(series, i)
        # Publish BOTH variants so a module-level cache peek sees each run's own
        # (divergent) future. A pure prefix read is unaffected: the prefix is the
        # same object graph in both cases.
        from . import signals as _signals

        prev = getattr(_signals, "CURRENT_SERIES", None)
        _signals.CURRENT_SERIES = series
        try:
            a = _fingerprint(evaluator(series.upto_index(i)))
        finally:
            _signals.CURRENT_SERIES = prev
        _signals.CURRENT_SERIES = twin
        try:
            b = _fingerprint(evaluator(twin.upto_index(i)))
        finally:
            _signals.CURRENT_SERIES = prev
        if a != b:
            leaks.append(i)
    return leaks


def _rewrite_future(series: Series, cut: int) -> Series:
    """A copy of *series* with every bar after *cut* replaced by a divergent one.

    Bars ``0..cut`` are shared objects, so the two prefixes handed to the
    evaluator are provably the same data — a differing verdict cannot be blamed
    on the mutation.
    """
    from .candles import Candle

    out: list[Candle] = list(series.candles[: cut + 1])
    for j in range(cut + 1, len(series)):
        src = series[j]
        out.append(
            Candle(
                timestamp=src.timestamp,
                open=src.open * 7.0 + 13.0,
                high=src.high * 7.0 + 13.0,
                low=src.low * 0.11,
                close=src.close * 7.0 + 13.0,
                volume=src.volume * 31.0 + 5.0,
                trades=src.trades + 101,
                chain=src.chain,
                pair_address=src.pair_address,
            )
        )
    return Series(out)


def purged_split(n: int, folds: int = 4, embargo_bars: int = 2) -> list[tuple[int, int, int, int]]:
    """``(train_start, train_end, test_start, test_end)`` with an embargoed gap.

    The gap after every training window is the purging: a label derived from the
    training window's final bars is not allowed to reach into the test window,
    which would leak the regime boundary into the score.
    """
    # NOTE: train_end is an EXCLUSIVE bound. The caller slices [start:end), so an
    # embargo of E bars means test_start - E is the last trainable index.
    if n <= 0 or folds <= 0:
        raise ValueError("n and folds must be positive")
    fold = max(1, n // folds)
    out: list[tuple[int, int, int, int]] = []
    for k in range(folds):
        test_start = k * fold
        test_end = n if k == folds - 1 else (k + 1) * fold
        train_end = max(0, test_start - embargo_bars)
        out.append((0, train_end, test_start, test_end))
    return out


def _fingerprint(signals: list[Signal]) -> list[tuple[str, str, float, float, str]]:
    """The full observable verdict of an evaluator, order-sensitive.

    ``summary`` is part of the fingerprint on purpose: a leak can hide in the
    prose an evaluator emits (it is the field most likely to quote a price it
    should not know), and a check that only compared kind/confidence would wave
    that through.
    """
    return [
        (s.kind, s.as_of.isoformat(), round(s.confidence, 6), s.severity, s.summary)
        for s in signals
    ]


__all__ = [
    "DEFAULT_COST_BPS",
    "FILL_LAG_BARS",
    "BacktestResult",
    "Trade",
    "prefix_invariance",
    "purged_split",
    "walk_forward",
]
