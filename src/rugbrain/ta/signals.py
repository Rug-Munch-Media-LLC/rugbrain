"""Signal evaluation: candles + indicators -> a scored, explainable finding.

Three signatures, each with an explicit trigger so a finding can be audited
rather than taken on faith:

* ``liquidity_collapse`` — volume has dried up relative to its own recent norm
  while price is falling. Thin books plus a downtrend is the pre-manipulation
  / pre-liquidation shape RugBrain cares about.
* ``abnormal_volume`` — a volume bar far outside its own trailing distribution.
  Spike *and* air-pocket are both flagged, with the direction separated.
* ``dump_pattern`` — a sharp, high-volume, oversold down move.

Everything is computed from a :class:`~rugbrain.ta.candles.Series` prefix, so a
finding is only ever produced from bars already observed at that bar's timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .candles import Candle, Series
from .indicators import atr, drawdown, ema, rsi, sma, volatility

#: A finding must clear this confidence to be reported at all.
MIN_CONFIDENCE = 0.30


@dataclass(frozen=True, slots=True)
class Signal:
    """One evaluated finding at a single point in time."""

    kind: str
    confidence: float
    severity: str
    as_of: datetime
    pair: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        """Reported or not.

        The bar is ``MIN_CONFIDENCE`` alone. Severity is *derived* from
        confidence, so gating on both is a double test that silently drops
        anything in the "low" band — a 0.40-confidence finding passed the
        confidence floor and was then discarded by the severity check, which
        makes ``MIN_CONFIDENCE`` a lie. Severity stays in the payload for
        triage; it does not decide what exists.
        """
        return self.confidence >= MIN_CONFIDENCE


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

    closes = series.closes()
    volumes = series.volumes()
    bar = series.last
    pair = f"{bar.chain}:{bar.pair_address}" if bar.pair_address else ""

    # The baseline EXCLUDES the bar under test. Including it lets a thin bar
    # pull its own baseline down and flatter the ratio — a bar at 40% of the
    # prior 50-bar mean would read as 0.405 against a baseline it had itself
    # dragged to 0.411, and the same contamination compounds over a sustained
    # collapse. Compare against history only.
    vol_ma = sma(volumes[:-1], baseline_period)
    rsi_vals = rsi(closes, rsi_period)
    atr_vals = atr(series.highs(), series.lows(), closes, atr_period)
    vol_vals = volatility(closes, 20)

    ref: dict[str, Any] = {
        "close": bar.close,
        "volume": bar.volume,
        "volume_baseline": _last(vol_ma),
        "volume_ratio": _ratio(bar.volume, _last(vol_ma)),
        "rsi14": _last(rsi_vals),
        "atr14": _last(atr_vals),
        "atr_pct": _pct(_last(atr_vals), bar.close),
        "realised_vol": _last(vol_vals),
        "drawdown": _last(drawdown(closes)),
    }
    signals: list[Signal] = []
    signals.extend(_liquidity_collapse(bar, ref, pair))
    signals.extend(_abnormal_volume(bar, ref, pair))
    signals.extend(_dump_pattern(closes, rsi_vals, atr_vals, bar, ref, pair))
    return [s for s in signals if s.is_actionable]


def evaluate_at(series: Series, index: int, **kwargs: Any) -> list[Signal]:
    """Evaluate the signatures as they stood at *index* — the backtest entry point."""
    return evaluate(series.upto_index(index), **kwargs)


#: Volume at or below this fraction of the 50-bar baseline counts as "collapsed".
VOLUME_COLLAPSE_RATIO = 0.35
#: A bar at or below this fraction counts as an air pocket.
VOLUME_DRY_RATIO = 0.10
#: Drawdown needed before thin-book pressure is treated as a loss, not a wobble.
DRAWDOWN_ALERT = -0.02


def _liquidity_collapse(bar: Candle, ref: dict[str, Any], pair: str) -> list[Signal]:
    ratio = ref["volume_ratio"]
    dd = ref["drawdown"]
    if ratio is None or bar.volume <= 0 or ratio > VOLUME_COLLAPSE_RATIO:
        return []
    price_down = bar.close < bar.open or (dd is not None and dd < DRAWDOWN_ALERT)
    if not price_down:
        return []
    # Confidence rises as volume dries up and as the loss deepens.
    thin = max(0.0, min(1.0, (VOLUME_COLLAPSE_RATIO - ratio) / VOLUME_COLLAPSE_RATIO))
    depth = max(0.0, min(1.0, abs(min(dd or 0.0, 0.0)) / 0.20))
    conf = round(0.55 * thin + 0.45 * depth, 4)
    return [
        Signal(
            kind="liquidity_collapse",
            confidence=conf,
            severity=_severity(conf),
            as_of=bar.timestamp,
            pair=pair,
            summary=(
                f"volume at {ratio:.0%} of its 50-bar mean while price is down "
                f"(drawdown {dd:.1%}); thin-book sell pressure"
            ),
            evidence=ref,
        )
    ]


def _abnormal_volume(bar: Candle, ref: dict[str, Any], pair: str) -> list[Signal]:
    ratio = ref["volume_ratio"]
    if ratio is None:
        return []
    # 1.8x..3.0x is ordinary flow on a DEX pair; flagging it would fire on
    # nearly every active hour and bury the real anomalies.
    if ratio < 3.0 and ratio > 1.8:
        return []
    spike = ratio >= 3.0
    conf = round(min(0.95, (ratio / 6.0) if spike else (1.0 - ratio) / 0.9), 4)
    direction = "spike" if spike else "collapse"
    return [
        Signal(
            kind="abnormal_volume",
            confidence=conf,
            severity=_severity(conf),
            as_of=bar.timestamp,
            pair=pair,
            summary=(
                f"volume {direction}: {ratio:.2f}x its 50-bar mean "
                f"({'buy' if bar.is_bullish else 'sell'} bar)"
            ),
            evidence=ref,
        )
    ]


def _dump_pattern(
    closes: list[float],
    rsi_vals: list[float | None],
    atr_vals: list[float | None],
    bar: Candle,
    ref: dict[str, Any],
    pair: str,
) -> list[Signal]:
    i = len(closes) - 1
    if i < 1:
        return []
    change = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
    rsi_v = rsi_vals[i]
    atr_v = atr_vals[i]
    if change > -0.03 or rsi_v is None or atr_v is None:
        return []
    sell_pressure = max(change / -0.10, 0.0)
    oversold = max((50.0 - rsi_v) / 50.0, 0.0)
    move_vs_atr = abs(change) / (atr_v / bar.close) if atr_v and bar.close else 0.0
    if move_vs_atr < 1.5:
        return []
    conf = round(
        min(0.95, 0.45 * sell_pressure + 0.30 * oversold + 0.25 * min(move_vs_atr / 4, 1)), 4
    )
    return [
        Signal(
            kind="dump_pattern",
            confidence=conf,
            severity=_severity(conf),
            as_of=bar.timestamp,
            pair=pair,
            summary=(
                f"close {change:.1%} on the bar, RSI {rsi_v:.0f}, "
                f"{move_vs_atr:.1f}x ATR — distribution / liquidation shape"
            ),
            evidence={
                **ref,
                "bar_change": round(change, 6),
                "move_atr_multiple": round(move_vs_atr, 3),
            },
        )
    ]


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


def _severity(conf: float) -> str:
    if conf >= 0.75:
        return "critical"
    if conf >= 0.55:
        return "high"
    if conf >= MIN_CONFIDENCE:
        return "medium"
    return "low"


def _last(vals: list[float | None]) -> float | None:
    return vals[-1] if vals else None


def _ratio(value: float, base: float | None) -> float | None:
    if base is None or base <= 0:
        return None
    return value / base


def _pct(value: float | None, close: float) -> float | None:
    return None if value is None or close == 0 else value / close


__all__ = ["MIN_CONFIDENCE", "Signal", "evaluate", "evaluate_at", "trend_state"]
