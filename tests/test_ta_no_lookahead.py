"""The no-lookahead proof.

These tests are written to FAIL if anyone introduces a peek at the future. The
two that matter most:

* ``test_truncation_invariance_proves_no_peek`` — a deliberately lookahead-aware
  evaluator (one that peeks at ``series[i+1]``) is fed to the same invariance
  check the production evaluator passes. If the check cannot catch the known-bad
  evaluator, the check is worthless, so that is asserted explicitly.
* ``test_signal_at_i_matches_signal_from_prefix_object`` — the signal computed
  during a walk-forward pass equals the signal computed from a prefix that
  literally cannot reach past bar ``i``.
"""

from __future__ import annotations

import pytest

from rugbrain.ta.backtest import (
    FILL_LAG_BARS,
    _rewrite_future,
    prefix_invariance,
    purged_split,
    walk_forward,
)
from rugbrain.ta.candles import Candle, Series
from rugbrain.ta.signals import Signal, evaluate, evaluate_at

HOUR = 3600


def _candles(n: int, *, seed: int = 7) -> list[Candle]:
    """Deterministic price path with authored regimes so signals actually fire.

    Unit tests only (never used for a reported result). A single smooth wave
    produces a *constant* volume_ratio, so the volume-delta signatures can never
    trigger and the backtest returns zero trades — a test that asserts trades
    exist must therefore author the regimes it needs.
    """
    from datetime import UTC, datetime

    out: list[Candle] = []
    price = 100.0
    for i in range(n):
        drift = 0.004 if 30 <= i < 90 else (-0.012 if 120 <= i < 190 else 0.001)
        price = max(1.0, price * (1.0 + drift))
        # Baselines then a volume air-pocket, then a 4x spike.
        if i < 30:
            vol = 1000.0
        elif i < 90:
            vol = 100.0  # ~10% of baseline -> abnormal_volume collapse
        elif i < 120:
            vol = 1000.0
        elif i < 190:
            vol = 5000.0  # 5x baseline -> abnormal_volume spike
        else:
            vol = 1000.0
        close = price
        out.append(
            Candle(
                timestamp=datetime.fromtimestamp(1_700_000_000 + i * HOUR, tz=UTC),
                open=price * 0.999,
                high=max(price, close) * 1.02,
                low=min(price, close) * 0.98,
                close=close,
                volume=vol,
                trades=10 + i % 5,
                chain="ethereum",
                pair_address="0xtest",
            )
        )
    return out


# ── the invariance check itself must have teeth ───────────────────────────


def test_in_prefix_lookahead_is_structurally_impossible() -> None:
    """WHY a direct in-prefix lookahead cannot happen here — asserted, not assumed.

    ``walk_forward`` hands the evaluator ``series.upto_index(i)``. That prefix's
    last valid index is ``i``, so ``prefix[i + 1]`` is an IndexError: there is
    no in-prefix bar to peek at. An evaluator wanting a future bar must go
    *around* the API — via a closure or a cache — and that route IS caught
    (``test_invariance_catches_a_side_channel_lookahead``).

    Pinning the IndexError keeps the guarantee honest: if someone later widens
    the API to hand over the full series, this test fails and forces the
    question to be reopened rather than silently reopened-by-refactor.
    """
    series = Series(_candles(200))
    prefix = series.upto_index(10)
    assert len(prefix) == 11
    with pytest.raises(IndexError):
        _ = prefix[11]


def _cache_peeking_signal(prefix: Series) -> list[Signal]:
    """A deliberately cheating evaluator that peeks via the module-level cache.

    This is the realistic accidental-leak shape: code that reaches for "the
    series" from a module global instead of using the prefix it was handed.
    """
    from rugbrain.ta import signals as sig

    full = getattr(sig, "CURRENT_SERIES", None)
    i = len(prefix) - 1
    if full is None or i + 1 >= len(full):
        return []
    # ILLEGAL: reads a bar after the decision point.
    return [Signal("cheat", 0.9, "high", prefix[i].timestamp, "p", str(full[i + 1].close))]


def test_invariance_catches_a_module_cache_lookahead() -> None:
    """The realistic leak vector — a module-level cache of the full series."""
    leaks = prefix_invariance(Series(_candles(200)), _cache_peeking_signal, checkpoints=12)
    assert leaks, "a module-cache lookahead went undetected"


def test_leak_hidden_only_in_the_summary_is_still_caught() -> None:
    """The fingerprint includes ``summary`` — a prose leak is a leak.

    An evaluator returning the SAME kind/confidence/severity but quoting a
    future price in its text has still read the future. A check that compared
    only the numeric fields would pass it.
    """

    def prose_leak(prefix: Series) -> list[Signal]:
        from rugbrain.ta import signals as sig

        full = getattr(sig, "CURRENT_SERIES", None)
        i = len(prefix) - 1
        if full is None or i + 1 >= len(full):
            return []
        return [
            Signal(
                "dump_pattern", 0.5, "medium", prefix[i].timestamp, "p", str(full[i + 1].close)
            )
        ]

    leaks = prefix_invariance(Series(_candles(200)), prose_leak, checkpoints=12)
    assert leaks, "a leak confined to the summary text went undetected"


def test_invariance_check_is_not_vacuous_on_the_real_evaluator() -> None:
    """A cheap sanity check: the rewrite really does change the future."""
    series = Series(_candles(200))
    twin = _rewrite_future(series, 100)
    assert len(twin) == len(series)
    assert [c.timestamp for c in twin.candles[:101]] == [c.timestamp for c in series.candles[:101]]
    assert twin[150].close != series[150].close


def test_real_evaluator_shows_no_leaks() -> None:
    """The shipped evaluator is causal: truncating the future changes nothing."""
    leaks = prefix_invariance(Series(_candles(300)), evaluate, checkpoints=15)
    assert leaks == [], f"lookahead leak at bar indices {leaks}"


def test_signal_at_i_equals_signal_from_a_prefix_object() -> None:
    """A walk-forward bar and a hand-built prefix of equal length agree exactly."""
    series = Series(_candles(200))
    for i in (60, 120, 180):
        from_walk = evaluate(series.upto_index(i))
        from_prefix = evaluate_at(series, i)
        assert [s.kind for s in from_walk] == [s.kind for s in from_prefix]
        assert [round(s.confidence, 9) for s in from_walk] == [
            round(s.confidence, 9) for s in from_prefix
        ]


def test_upto_cannot_return_a_future_bar() -> None:
    series = Series(_candles(50))
    prefix = series.upto(series[30].timestamp)
    assert len(prefix) == 31
    assert prefix.last.timestamp == series[30].timestamp
    with pytest.raises(IndexError):
        series.upto_index(50)


# ── fills must not be back-filled at the signal price ───────────────────────


def test_fill_lags_the_signal_bar() -> None:
    """A trade's entry is strictly AFTER the signal's as-of timestamp."""
    series = Series(_candles(300))
    result = walk_forward(series, evaluate, hold_bars=3, warmup_bars=40)
    for trade in result.trades:
        assert trade.entry_time > trade.signal_as_of
    assert result.bars == 300


def test_fill_lag_constant_is_one_bar() -> None:
    assert FILL_LAG_BARS == 1


def test_costs_are_charged_per_trade() -> None:
    series = Series(_candles(300))
    result = walk_forward(series, evaluate, hold_bars=2, warmup_bars=40, cost_bps=50.0)
    assert result.trades
    assert all(t.costs_pct == 0.5 for t in result.trades)
    assert all(t.return_pct < 0.5 for t in result.trades)


# ── purged / embargoed split ──────────────────────────────────────────────


def test_purged_split_embargoes_a_gap_after_training() -> None:
    splits = purged_split(100, folds=4, embargo_bars=3)
    for train_start, train_end, test_start, _test_end in splits:
        if test_start == 0:
            continue
        assert train_end <= test_start - 3, "training window touched the test window"
    assert splits[-1][3] == 100, "last fold must reach the end of the series"


def test_purged_split_covers_every_bar_exactly_once_in_test() -> None:
    n, folds = 100, 4
    covered = [i for _a, _b, ts, te in purged_split(n, folds=folds) for i in range(ts, te)]
    assert covered == list(range(n))


def test_purged_split_rejects_bad_args() -> None:
    with pytest.raises(ValueError):
        purged_split(0, folds=4)
    with pytest.raises(ValueError):
        purged_split(10, folds=0)
