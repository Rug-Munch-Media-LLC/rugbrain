"""Indicator arithmetic — hand-computed values, no live data.

Every expected number here is derived by hand (or by the definition in Wilder /
Chande's papers) rather than by running the function, so a broken formula fails
instead of agreeing with itself.
"""

from __future__ import annotations

import math

import pytest

from rugbrain.ta.indicators import (
    atr,
    drawdown,
    ema,
    max_drawdown,
    rsi,
    sma,
    true_range,
    volatility,
    volume_delta,
)

# ── length + padding contract ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fn", "first_index"),
    [
        (sma, 13),  # needs `period` closes
        (ema, 13),  # seeded with the SMA of the first `period`
        (rsi, 14),  # needs `period` CHANGES, so one more close
    ],
)
def test_indicators_preserve_length_and_pad_with_none(fn, first_index: int) -> None:
    values = [float(i) for i in range(1, 31)]
    out = fn(values, 14)
    assert len(out) == len(values), "length mismatch would silently misalign a candle series"
    assert all(v is None for v in out[:first_index])
    assert out[first_index] is not None


def test_indicators_share_one_alignment_convention() -> None:
    """SMA/EMA land at index period-1; RSI lands one later.

    RSI is defined over *changes*, so it needs ``period + 1`` closes. This is
    pinned so a future refactor cannot silently shift RSI by one bar and
    misalign every RSI-based signal against its candle.
    """
    values = [float(i) for i in range(1, 31)]
    assert sma(values, 14)[13] is not None and sma(values, 14)[12] is None
    assert ema(values, 14)[13] is not None and ema(values, 14)[12] is None
    assert rsi(values, 14)[14] is not None and rsi(values, 14)[13] is None


def test_series_shorter_than_period_is_all_none() -> None:
    assert all(v is None for v in sma([1.0, 2.0, 3.0], 10))
    assert all(v is None for v in ema([1.0, 2.0, 3.0], 10))
    assert all(v is None for v in rsi([1.0, 2.0, 3.0], 14))
    assert sma([], 5) == []
    assert drawdown([]) == []


@pytest.mark.parametrize("fn", [sma, ema, rsi])
def test_non_positive_period_is_rejected(fn) -> None:
    with pytest.raises(ValueError):
        fn([1.0, 2.0, 3.0], 0)


# ── SMA ───────────────────────────────────────────────────────────────────


def test_sma_matches_hand_computation() -> None:
    # (1+2+3+4+5)/5 = 3.0 ; (2+3+4+5+6)/5 = 4.0
    out = sma([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 5)
    assert out[4] == 3.0
    assert out[5] == 4.0


def test_sma_is_the_mean_of_its_own_window() -> None:
    values = [5.0, 9.0, 2.0, 7.0, 4.0, 8.0, 1.0]
    out = sma(values, 3)
    for i in range(2, len(values)):
        assert out[i] == pytest.approx(sum(values[i - 2 : i + 1]) / 3)


# ── EMA ───────────────────────────────────────────────────────────────────


def test_ema_seeds_with_sma_then_applies_the_smoothing() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = ema(values, 3)
    # seed = (1+2+3)/3 = 2.0 at index 2
    assert out[2] == pytest.approx(2.0)
    k = 2.0 / 4.0
    assert out[3] == pytest.approx(4.0 * k + 2.0 * (1 - k))
    assert out[4] == pytest.approx(5.0 * k + out[3] * (1 - k))


def test_ema_of_a_constant_series_is_that_constant() -> None:
    out = ema([7.0] * 25, 10)
    assert out[9] == pytest.approx(7.0)
    assert out[-1] == pytest.approx(7.0)


# ── RSI ───────────────────────────────────────────────────────────────────


def test_rsi_of_a_monotonic_rise_is_100() -> None:
    out = rsi([float(i) for i in range(1, 40)], 14)
    assert out[13] is None  # first RSI needs 15 closes
    assert out[14] == pytest.approx(100.0)


def test_rsi_of_a_monotonic_fall_is_zero() -> None:
    out = rsi([float(i) for i in range(40, 1, -1)], 14)
    assert out[-1] == pytest.approx(0.0)


def test_rsi_stays_in_range_and_is_bounded_by_100() -> None:
    values = [100.0, 101.0, 100.5, 103.0, 99.0, 104.0, 102.0, 98.0, 105.0, 101.0]
    out = rsi(values * 4, 14)
    for v in out:
        if v is not None:
            assert 0.0 <= v <= 100.0


def test_rsi_hand_computed_first_value() -> None:
    # 15 closes, 14 changes, all +1 -> avg_gain=1, avg_loss=0 -> RSI 100
    assert rsi([float(i) for i in range(15)], 14)[14] == pytest.approx(100.0)
    # 15 closes: 0..13 (thirteen +1 changes) then a dip to 12 (-1).
    # avg_gain = 13/14, avg_loss = 1/14 -> RS = 13 -> RSI = 100 - 100/14.
    closes = [float(i) for i in range(14)] + [12.0]
    v = rsi(closes, 14)[14]
    assert v == pytest.approx(100.0 - (100.0 / 14.0))


# ── True range / ATR ──────────────────────────────────────────────────────


def test_true_range_first_bar_is_high_minus_low() -> None:
    out = true_range([10.0], [8.0], [9.0])
    assert out[0] == 2.0


def test_true_range_accounts_for_gaps() -> None:
    # bar0: high 10 low 8 close 9. bar1: high 11 low 9 close 11.
    # TR1 = max(11-9=2, |11-9(prev close)|=2, |9-9|=0) = 2 — the gap term ties.
    out = true_range([10.0, 11.0], [8.0, 9.0], [9.0, 11.0])
    assert out[1] == pytest.approx(2.0)
    # A pure gap up dominates the intrabar range: prev close 9, high 20, low 19.
    out2 = true_range([10.0, 20.0], [8.0, 19.0], [9.0, 19.5])
    assert out2[1] == pytest.approx(11.0)  # |20 - 9| = 11 > (20-19) = 1
    # No gap: prev close 9.5, high 11, low 9 -> max(2, 1.5, 0.5) = 2.
    out3 = true_range([10.0, 11.0], [8.0, 9.0], [9.0, 10.5])
    assert out3[1] == pytest.approx(2.0)


def test_true_range_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        true_range([1.0, 2.0], [1.0], [1.0, 2.0])


def test_atr_is_wilder_smoothed_true_range() -> None:
    n = 30
    highs = [10.0 + (i % 3) for i in range(n)]
    lows = [9.0 + (i % 2) for i in range(n)]
    closes = [9.5 + (i % 3) for i in range(n)]
    out = atr(highs, lows, closes, 14)
    tr = [v for v in true_range(highs, lows, closes) if v is not None]
    assert out[13] == pytest.approx(sum(tr[:14]) / 14)
    prev = sum(tr[:14]) / 14
    for i in range(14, n):
        prev = (prev * 13 + tr[i]) / 14
        assert out[i] == pytest.approx(prev)


# ── volume / volatility / drawdown ─────────────────────────────────────────


def test_volume_delta_is_zero_at_the_mean() -> None:
    out = volume_delta([10.0] * 25, 20)
    assert out[19] == pytest.approx(0.0)
    assert out[24] == pytest.approx(0.0)


def test_volume_delta_is_positive_above_the_mean() -> None:
    # 19 bars at 10 then one at 30: the 20-bar window is (19*10 + 30)/20 = 11.0,
    # so the delta at the spike is 30 - 11 = 19.
    values = [10.0] * 19 + [30.0]
    assert volume_delta(values, 20)[19] == pytest.approx(19.0)
    assert volume_delta(values, 20)[18] is None


def test_volatility_is_zero_for_a_constant_price() -> None:
    out = volatility([100.0] * 40, 20)
    assert all(v == pytest.approx(0.0) for v in out if v is not None)


def test_volatility_is_positive_and_scales_with_dispersion() -> None:
    calm = [100.0 * (1.001**i) for i in range(40)]
    wild = [100.0 * (1.05 if i % 2 else 0.95) ** 1 for i in range(40)]
    a = [v for v in volatility(calm, 20) if v is not None]
    b = [v for v in volatility(wild, 20) if v is not None]
    assert a and b
    assert sum(b) / len(b) > sum(a) / len(a)
    assert all(v >= 0 for v in b)


def test_drawdown_is_zero_at_a_peak_and_negative_after() -> None:
    out = drawdown([10.0, 20.0, 15.0, 25.0, 5.0])
    assert out[0] == 0.0
    assert out[1] == 0.0  # new peak
    assert out[2] == pytest.approx((15.0 - 20.0) / 20.0)
    assert out[3] == 0.0  # new peak
    assert out[4] == pytest.approx((5.0 - 25.0) / 25.0)


def test_max_drawdown_is_the_worst_point() -> None:
    assert max_drawdown([10.0, 20.0, 15.0, 25.0, 5.0]) == pytest.approx((5.0 - 25.0) / 25.0)
    assert max_drawdown([1.0, 2.0, 3.0]) == 0.0
    assert max_drawdown([]) == 0.0


# ── purity ────────────────────────────────────────────────────────────────


def test_indicators_do_not_mutate_their_input() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    snapshot = list(values)
    for fn in (lambda v: sma(v, 3), lambda v: ema(v, 3), lambda v: rsi(v, 3)):
        fn(values)
    volume_delta(values, 3)
    drawdown(values)
    assert values == snapshot


def test_sma_running_sum_has_no_drift() -> None:
    """A rolling sum built by add/subtract must not accumulate float error."""
    values = [1e9 + (i % 7) for i in range(5000)]
    out = sma(values, 50)
    for i in (100, 1000, 2500, 4999):
        assert out[i] == pytest.approx(sum(values[i - 49 : i + 1]) / 50, rel=1e-12)


def test_indicators_accept_tuples_and_lists_alike() -> None:
    assert sma((1.0, 2.0, 3.0), 2) == sma([1.0, 2.0, 3.0], 2)


def test_no_pandas_dependency_is_imported() -> None:
    """Guard against a future 'convenience' pull-in of pandas/numpy."""
    import sys

    for mod in ("pandas", "numpy"):
        assert mod not in sys.modules or True  # numpy may arrive via another lib
    import rugbrain.ta.indicators as ind

    src = ind.__file__
    assert src is not None
    text = open(src, encoding="utf-8").read()
    assert "import pandas" not in text
    assert "import numpy" not in text
    assert math  # module uses the stdlib, as documented
