"""Point-in-time backtest harness. Reads the past or it does not run.

The contract this module exists to enforce:

    **a signal at bar ``i`` may only read candles ``0..i`` — never ``i+1..``.**

That is not a convention the caller is trusted to honour. It is structural:

1. :func:`walk_forward` iterates bars and calls the evaluator with
   ``series.upto_index(i)``. The evaluator receives a *prefix object*, so there
   is no index in reach that points forward.
2. :func:`prefix_invariance` (in :mod:`rugbrain.ta.no_lookahead`) re-derives
   every past bar's signal from a **truncated** copy of the series. If any stage
   peeked ahead, appending future bars would change a past answer and the check
   fails. That is the test that would fail if someone peeked.
3. Entry fills happen at the **next** bar's open, never at the signal bar's
   close, so a fill is not back-filled with the price that produced the signal.
4. Costs are charged per round trip, so a high-frequency "always on" strategy
   cannot look profitable purely by trading more.

One job here: turn bars into trades and a result. The leak *detector* lives in
:mod:`rugbrain.ta.no_lookahead`; it takes an evaluator and tries to catch it
cheating, which is a different question from "how did the strategy do".

Purged/embargoed splitting: :func:`purged_split` withholds a gap of
``embargo_bars`` after each training window so a label computed from a training
window's last bar cannot overlap the first out-of-sample bar.
"""

from __future__ import annotations

from collections.abc import Callable

from .candles import Series
from .no_lookahead import prefix_invariance
from .results import BacktestResult, Trade
from .signals import Signal, evaluate

#: Fills happen one bar after the signal — see module docstring point 3.
FILL_LAG_BARS = 1
#: Round-trip cost as a fraction of notional (0.1% each way is realistic on DEX).
DEFAULT_COST_BPS = 10.0


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
