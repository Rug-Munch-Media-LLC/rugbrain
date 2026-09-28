"""The no-lookahead check: does an evaluator read the future?

Split out of :mod:`rugbrain.ta.backtest` because this is a *verification*
concern — it takes an evaluator and tries to catch it cheating — while
``backtest`` earns money-shaped numbers from a walk-forward pass. Two jobs.

The mechanism, stated once so the code below can stay short:

    build two full series that are byte-identical on bars ``0..i`` and
    deliberately divergent after ``i``; run the evaluator on each one's prefix
    through ``i``; a causal evaluator MUST return an identical answer twice.

Any difference is, by construction, a read of the future — including a
side-channel read through a closure over the parent, a module-level cache, or a
global. The check is therefore sensitive to the realistic accidental leak, not
only to an off-by-one inside the indicator maths.
"""

from __future__ import annotations

from collections.abc import Callable

from .candles import Candle, Series
from .signals import Signal, evaluate

__all__ = ["fingerprint", "prefix_invariance", "rewrite_future"]

#: A module-level cache of the full series is the realistic leak. The check
#: publishes each run's own (divergent) series here, so an evaluator peeking at
#: a global sees two different futures and its verdicts diverge.
_PUBLISH_TARGET = "rugbrain.ta.signals"


def fingerprint(signals: list[Signal]) -> list[tuple[str, str, float, float, str]]:
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


def rewrite_future(series: Series, cut: int) -> Series:
    """A copy of *series* with every bar after *cut* replaced by a divergent one.

    Bars ``0..cut`` are shared objects, so the two prefixes handed to the
    evaluator are provably the same data — a differing verdict cannot be blamed
    on the mutation.
    """
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


def prefix_invariance(
    series: Series,
    evaluator: Callable[[Series], list[Signal]] = evaluate,
    *,
    checkpoints: int = 12,
    warmup_bars: int = 21,
) -> list[int]:
    """Bar indices whose signal changed when the FUTURE was rewritten.

    Returns the offending bar indices; ``[]`` means no lookahead was observed.
    This is the executable form of the rule: run it against whatever evaluator
    ships, not only in a unit test.

    An evaluator that peeks through a closure over a *fixed parent* is not
    detectable by construction (it looks at the same bars both times) — the
    prefix API is what defends against that class, and ``Series.upto_index``
    raising ``IndexError`` past the cut is the mechanism, pinned by
    ``test_in_prefix_lookahead_is_structurally_impossible``.
    """
    import importlib

    n = len(series)
    if n <= warmup_bars + 1:
        return []
    leaks: list[int] = []
    start, stop = warmup_bars, n - 1
    step = max(1, (stop - start) // max(checkpoints, 1))
    signals_mod = importlib.import_module(_PUBLISH_TARGET)
    for i in range(start, stop, step):
        twin = rewrite_future(series, i)
        prev = getattr(signals_mod, "CURRENT_SERIES", None)
        # A pure prefix read is unaffected: the prefix is the same object graph
        # in both cases.
        a = _run_published(signals_mod, series, i, evaluator, prev)
        b = _run_published(signals_mod, twin, i, evaluator, prev)
        if a != b:
            leaks.append(i)
    return leaks


def _run_published(
    signals_mod: object, full: Series, cut: int, evaluator: Callable[..., object], prev: object
) -> list[tuple[str, str, float, float, str]]:
    """Evaluate ``full``'s prefix with ``full`` published as the module cache."""
    signals_mod.CURRENT_SERIES = full
    try:
        return fingerprint(evaluator(full.upto_index(cut)))  # type: ignore[arg-type]
    finally:
        signals_mod.CURRENT_SERIES = prev
