"""Signal evaluation: candles + indicators -> a scored, explainable finding.

One job: build the evidence for the FINAL bar of a series prefix and dispatch it
to the three signatures in :mod:`rugbrain.ta.signatures`. The signatures
themselves own their triggers and their confidence maths; this module owns the
point-in-time discipline that makes them safe to trust.

Everything is computed from a :class:`~rugbrain.ta.candles.Series` prefix, so a
finding is only ever produced from bars already observed at that bar's timestamp.
"""

from __future__ import annotations

from typing import Any

from .candles import Series
from .indicators import atr, drawdown, ema, rsi, sma, volatility
from .signatures import (
    MIN_CONFIDENCE,
    Signal,
    abnormal_volume,
    dump_pattern,
    liquidity_collapse,
    severity,
)

#: Kept as a module-level alias: this was ``signals._severity`` and callers
#: (tests, triage code) import it from here.
_severity = severity

__all__ = ["MIN_CONFIDENCE", "Signal", "evaluate", "evaluate_at", "trend_state"]


def _last(vals: list[float | None]) -> float | None:
    return vals[-1] if vals else None


def _ratio(value: float, base: float | None) -> float | None:
    if base is None or base <= 0:
        return None
    return value / base


def _pct(value: float | None, close: float) -> float | None:
    return None if value is None or close == 0 else value / close


def _evidence(series: Series, bar: Any, baseline_period: int) -> dict[str, Any]:
    """Derive the indicator set for *bar*, the final bar of *series*.

    The volume baseline EXCLUDES the bar under test. Including it lets a thin
    bar pull its own baseline down and flatter the ratio — a bar at 40% of the
    prior 50-bar mean would read as 0.405 against a baseline it had itself
    dragged to 0.411, and the same contamination compounds over a sustained
    collapse. Compare against history only.
    """
    closes, volumes = series.closes(), series.volumes()
    vol_ma = _last(sma(volumes[:-1], baseline_period))
    atr_v = _last(atr(series.highs(), series.lows(), closes, 14))
    return {
        "close": bar.close,
        "volume": bar.volume,
        "volume_baseline": vol_ma,
        "volume_ratio": _ratio(bar.volume, vol_ma),
        "rsi14": _last(rsi(closes, 14)),
        "atr14": atr_v,
        "atr_pct": _pct(atr_v, bar.close),
        "realised_vol": _last(volatility(closes, 20)),
        "drawdown": _last(drawdown(closes)),
    }


def evaluate(
    series: Series,
    *,
    volume_period: int = 20,
    baseline_period: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    price_period: int = 20,
) -> list[Signal]:
    """Evaluate every signature at the FINAL bar of *series* only.

    Deliberately single-bar: a caller that wants the signal at bar ``i`` passes
    ``series.upto_index(i)``. That makes the point-in-time discipline the
    caller's explicit choice rather than something the function can get wrong.

    ``baseline_period`` is deliberately LONGER than ``volume_period``. A volume
    baseline drawn from the same window as the bar being judged is contaminated
    by the regime it is meant to detect: after 20 consecutive thin hours, a
    20-bar mean has fallen as far as the bars did, the ratio returns to ~1.0, and
    a genuine liquidity collapse scores as normal. The longer baseline is what
    makes a *sustained* collapse visible.
    """
    needed = max(volume_period, baseline_period, rsi_period, atr_period, price_period) + 1
    if len(series) < needed:
        return []

    bar = series.last
    pair = f"{bar.chain}:{bar.pair_address}" if bar.pair_address else ""
    ev = _evidence(series, bar, baseline_period)
    signals: list[Signal] = []
    signals.extend(liquidity_collapse(bar, ev, pair))
    signals.extend(abnormal_volume(bar, ev, pair))
    signals.extend(dump_pattern(bar, ev, pair, closes=series.closes()))
    return [s for s in signals if s.is_actionable]


def evaluate_at(series: Series, index: int, **kwargs: Any) -> list[Signal]:
    """Evaluate the signatures as they stood at *index* — the backtest entry point."""
    return evaluate(series.upto_index(index), **kwargs)


def trend_state(closes: list[float], fast: int = 9, slow: int = 21) -> str:
    """``bull`` / ``bear`` / ``flat`` from an EMA fast/slow cross at the last bar."""
    if len(closes) < slow:
        return "flat"
    f, s = _last(ema(closes, fast)), _last(ema(closes, slow))
    if f is None or s is None:
        return "flat"
    if f > s * 1.001:
        return "bull"
    if f < s * 0.999:
        return "bear"
    return "flat"
