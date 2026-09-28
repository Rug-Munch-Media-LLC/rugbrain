"""Signal evaluation: the three signatures fire on the shapes they claim to.

Synthetic candles are used HERE ONLY — never for a reported result. Each test
authors the exact regime a signature is meant to detect, so a broken threshold
or a mis-wired indicator shows up as a signature that fails to fire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.ta.candles import Candle, Series
from rugbrain.ta.signals import MIN_CONFIDENCE, Signal, evaluate, evaluate_at, trend_state

START = datetime(2026, 9, 1, tzinfo=UTC)


def _build(
    *,
    n: int = 80,
    drift: float = 0.0,
    volumes: list[float] | None = None,
    spike_last: float | None = None,
    drop_last: float | None = None,
) -> Series:
    """A flat-ish baseline with an authored final bar."""
    candles: list[Candle] = []
    price = 100.0
    vols = volumes if volumes is not None else [1000.0] * n
    for i in range(n):
        open_p = price
        close_p = price * (1.0 + drift)
        vol = vols[i] if i < len(vols) else 1000.0
        if i == n - 1 and drop_last is not None:
            close_p = open_p * (1.0 + drop_last)
        if i == n - 1 and spike_last is not None:
            vol = spike_last
        high = max(open_p, close_p) * 1.01
        low = min(open_p, close_p) * 0.99
        candles.append(
            Candle(
                timestamp=START + timedelta(hours=i),
                open=open_p,
                high=high,
                low=low,
                close=close_p,
                volume=vol,
                trades=5,
                chain="ethereum",
                pair_address="0xsig",
            )
        )
        price = close_p
    return Series(candles)


def _kinds(signals: list[Signal]) -> set[str]:
    return {s.kind for s in signals}


# ── gating ────────────────────────────────────────────────────────────────


def test_short_series_produces_nothing() -> None:
    assert evaluate(_build(n=10)) == []


def test_shorter_than_the_baseline_window_produces_nothing() -> None:
    """The volume baseline is 50 bars; a 30-bar series cannot support a verdict."""
    assert evaluate(_build(n=30)) == []


def test_every_returned_signal_is_actionable() -> None:
    for s in evaluate(_build(n=80, drop_last=-0.06, spike_last=9000.0)):
        assert s.confidence >= MIN_CONFIDENCE
        assert s.severity in ("medium", "high", "critical")


def test_calm_series_produces_nothing() -> None:
    assert evaluate(_build(n=80)) == []


# ── abnormal volume ───────────────────────────────────────────────────────


def test_volume_spike_is_flagged() -> None:
    sigs = _kinds(evaluate(_build(n=80, spike_last=5000.0)))
    assert "abnormal_volume" in sigs


def test_volume_air_pocket_is_flagged() -> None:
    vols = [1000.0] * 60 + [50.0] * 20
    sigs = _kinds(evaluate(_build(n=80, volumes=vols)))
    assert "abnormal_volume" in sigs


def test_moderate_volume_is_not_flagged() -> None:
    """A 2x bar is normal flow, not a signature — this bounds false positives."""
    assert "abnormal_volume" not in _kinds(evaluate(_build(n=80, spike_last=2000.0)))


def test_the_baseline_excludes_the_bar_under_test() -> None:
    """A bar is judged against history, never against a mean it helped form.

    Including the bar in its own baseline lets it flatter itself: 5000 on a
    1000 baseline reads 5.0x, but against a baseline that already absorbed the
    spike it would read 4.63x. Over a sustained collapse that compounds.
    """
    sig = next(s for s in evaluate(_build(n=80, spike_last=5000.0)) if s.kind == "abnormal_volume")
    assert sig.evidence["volume_ratio"] == pytest.approx(5.0)
    assert sig.evidence["volume_baseline"] == pytest.approx(1000.0)


def test_abnormal_volume_records_the_ratio() -> None:
    sig = next(s for s in evaluate(_build(n=80, spike_last=5000.0)) if s.kind == "abnormal_volume")
    # The baseline EXCLUDES the bar under test, so it is the prior 50 bars' mean.
    assert sig.evidence["volume_baseline"] == pytest.approx(1000.0)
    assert sig.evidence["volume_ratio"] == pytest.approx(5.0)
    assert "x its 50-bar mean" in sig.summary


def test_a_sustained_collapse_is_still_visible_against_its_own_baseline() -> None:
    """The contamination problem this baseline choice exists to solve.

    Against a 20-bar mean, 20 consecutive thin hours drag the baseline down with
    them and the ratio returns to ~1.0 — a real liquidity collapse would score as
    normal flow. The 50-bar window keeps enough pre-collapse history for the
    ratio to stay low.
    """
    from rugbrain.ta.indicators import sma

    vols = [1000.0] * 60 + [60.0] * 20
    short = sma(vols, 20)[-1]
    long = sma(vols, 50)[-1]
    assert 60.0 / long < 0.2, "the long baseline must still register the collapse"
    assert short is not None and 60.0 / short > 0.9, "the short baseline is fully contaminated"


# ── liquidity collapse ────────────────────────────────────────────────────


def test_liquidity_collapse_fires_when_volume_dries_up_into_a_decline() -> None:
    vols = [1000.0] * 60 + [60.0] * 20
    sigs = _kinds(evaluate(_build(n=80, volumes=vols, drift=-0.01)))
    assert "liquidity_collapse" in sigs


def test_liquidity_collapse_needs_price_pressure_not_just_thin_volume() -> None:
    vols = [1000.0] * 60 + [60.0] * 20
    assert "liquidity_collapse" not in _kinds(evaluate(_build(n=80, volumes=vols, drift=+0.01)))


def test_liquidity_collapse_confidence_rises_as_thin_book_deepens() -> None:
    """Both regimes must clear the 35% gate, then rank by how bad they are.

    ``vols`` must reach the LAST bar for the ratio to be measured at all, so the
    volume series is the full length and the ramp is carried by the price drift.
    """

    def regime(thin: float, drift: float) -> Signal | None:
        vols = [1000.0] * 79 + [thin]
        sigs = evaluate(_build(n=80, volumes=vols, drift=drift))
        return next((s for s in sigs if s.kind == "liquidity_collapse"), None)

    mild = regime(300.0, -0.002)   # ratio 0.30, just inside the 35% gate
    severe = regime(30.0, -0.05)    # ratio 0.03, a near-total air pocket
    assert mild is not None, "a 30%-of-baseline collapse should clear the 35% gate"
    assert severe is not None
    assert severe.confidence > mild.confidence


def test_liquidity_collapse_below_the_gate_is_not_reported() -> None:
    """A bar at 40% of baseline is thinning, not yet a collapse.

    The 35% gate is a deliberate false-positive bound: a pair running at 40% of
    its own recent norm is often simply quiet, and calling that a collapse on
    every quiet hour would bury the real events.
    """
    vols = [1000.0] * 79 + [400.0]
    assert "liquidity_collapse" not in _kinds(evaluate(_build(n=80, volumes=vols, drift=-0.05)))


# ── dump pattern ──────────────────────────────────────────────────────────


def test_dump_pattern_fires_on_a_sharp_large_oversold_drop() -> None:
    sigs = _kinds(evaluate(_build(n=80, drop_last=-0.12)))
    assert "dump_pattern" in sigs


def test_dump_pattern_quiet_on_a_small_dip() -> None:
    assert "dump_pattern" not in _kinds(evaluate(_build(n=80, drop_last=-0.005)))


def test_dump_pattern_carries_the_atr_multiple() -> None:
    sig = next(s for s in evaluate(_build(n=80, drop_last=-0.12)) if s.kind == "dump_pattern")
    assert sig.evidence["move_atr_multiple"] >= 1.5
    assert sig.evidence["bar_change"] < 0


def test_signal_evidence_carries_the_indicator_snapshot() -> None:
    sigs = evaluate(_build(n=80, drop_last=-0.12, spike_last=6000.0))
    assert sigs
    for key in ("close", "volume_ratio", "rsi14", "atr14", "atr_pct", "drawdown"):
        assert key in sigs[0].evidence


# ── point-in-time API ─────────────────────────────────────────────────────


def test_evaluate_at_equals_evaluate_on_the_prefix() -> None:
    s = _build(n=80, drop_last=-0.12, spike_last=5000.0)
    assert [x.kind for x in evaluate_at(s, 59)] == [x.kind for x in evaluate(s.upto_index(59))]


def test_signal_as_of_equals_the_last_bar_of_the_prefix() -> None:
    s = _build(n=80, drop_last=-0.12, spike_last=5000.0)
    for sig in evaluate_at(s, 59):
        assert sig.as_of == s[59].timestamp


def test_evaluate_at_rejects_an_out_of_range_index() -> None:
    s = _build(n=80)
    with pytest.raises(IndexError):
        evaluate_at(s, len(s))
    with pytest.raises(IndexError):
        evaluate_at(s, -1)


# ── trend ─────────────────────────────────────────────────────────────────


def test_trend_state_reports_bull_bear_flat() -> None:
    bull = [100.0 * (1.01**i) for i in range(60)]
    bear = [100.0 * (0.99**i) for i in range(60)]
    flat = [100.0] * 60
    assert trend_state(bull) == "bull"
    assert trend_state(bear) == "bear"
    assert trend_state(flat) == "flat"


def test_trend_state_is_flat_when_the_series_is_too_short() -> None:
    assert trend_state([1.0, 2.0, 3.0]) == "flat"


# ── shape ─────────────────────────────────────────────────────────────────


def test_signal_fields_are_all_populated() -> None:
    for sig in evaluate(_build(n=80, drop_last=-0.12, spike_last=5000.0)):
        assert sig.pair == "ethereum:0xsig"
        assert sig.summary
        assert isinstance(sig.evidence, dict) and sig.evidence
        assert sig.as_of.tzinfo is not None


def test_signal_is_hashable_and_frozen() -> None:
    sig = Signal("k", 0.5, "medium", START, "p", "s")
    with pytest.raises((AttributeError, TypeError)):
        sig.kind = "other"  # type: ignore[misc]


# ── regression: the actionability gate ───────────────────────────────────


def test_min_confidence_is_the_only_gate() -> None:
    """A finding above MIN_CONFIDENCE is reported whatever its severity band.

    Severity is DERIVED from confidence. Gating on both meant a 0.40-confidence
    finding cleared MIN_CONFIDENCE and was then silently dropped by the severity
    check — the constant was a lie, and the drop was invisible because the
    signal was simply absent. Pinned so the two can never drift apart again.
    """
    sig = Signal("liquidity_collapse", 0.40, "low", START, "e:0x", "s")
    assert sig.confidence >= MIN_CONFIDENCE
    assert sig.is_actionable, "a low-severity finding above the floor must still be reported"


def test_below_min_confidence_is_never_reported() -> None:
    assert not Signal("x", MIN_CONFIDENCE - 0.01, "low", START, "p", "s").is_actionable


def test_severity_bands_are_ordered() -> None:
    from rugbrain.ta.signals import _severity

    assert _severity(0.95) == "critical"
    assert _severity(0.60) == "high"
    assert _severity(0.35) == "medium"
    assert _severity(0.05) == "low"
