"""The three signal signatures and the finding type they produce.

One job: decide *whether* a bar matches a known shape and with what confidence.
The evaluator that gathers indicators and dispatches to these lives in
:mod:`rugbrain.ta.signals`.

Each signature has an explicit trigger so a finding can be audited rather than
taken on faith:

* ``liquidity_collapse`` — volume has dried up relative to its own recent norm
  while price is falling. Thin books plus a downtrend is the pre-manipulation
  / pre-liquidation shape RugBrain cares about.
* ``abnormal_volume`` — a volume bar far outside its own trailing distribution.
  Spike *and* air-pocket are both flagged, with the direction separated.
* ``dump_pattern`` — a sharp, high-volume, oversold down move.

Every signature receives an evidence mapping derived from a series
*prefix*, so a finding is only ever produced from bars already observed at that
bar's timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .candles import Candle

#: A finding must clear this confidence to be reported at all.
MIN_CONFIDENCE = 0.30

#: Volume at or below this fraction of the 50-bar baseline counts as "collapsed".
VOLUME_COLLAPSE_RATIO = 0.35
#: A bar at or below this fraction counts as an air pocket.
VOLUME_DRY_RATIO = 0.10
#: Drawdown needed before thin-book pressure is treated as a loss, not a wobble.
DRAWDOWN_ALERT = -0.02


def severity(conf: float) -> str:
    """Map a confidence to a triage band. Purely descriptive — see below."""
    if conf >= 0.75:
        return "critical"
    if conf >= 0.55:
        return "high"
    if conf >= MIN_CONFIDENCE:
        return "medium"
    return "low"


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


def liquidity_collapse(bar: Candle, ev: dict[str, Any], pair: str) -> list[Signal]:
    """Thin book + falling price. Confidence rises as volume dries and the loss deepens."""
    ratio, dd = ev["volume_ratio"], ev["drawdown"]
    if ratio is None or bar.volume <= 0 or ratio > VOLUME_COLLAPSE_RATIO:
        return []
    price_down = bar.close < bar.open or (dd is not None and dd < DRAWDOWN_ALERT)
    if not price_down:
        return []
    thin = max(0.0, min(1.0, (VOLUME_COLLAPSE_RATIO - ratio) / VOLUME_COLLAPSE_RATIO))
    depth = max(0.0, min(1.0, abs(min(dd or 0.0, 0.0)) / 0.20))
    conf = round(0.55 * thin + 0.45 * depth, 4)
    return [
        Signal(
            kind="liquidity_collapse",
            confidence=conf,
            severity=severity(conf),
            as_of=bar.timestamp,
            pair=pair,
            summary=(
                f"volume at {ratio:.0%} of its 50-bar mean while price is down "
                f"(drawdown {dd:.1%}); thin-book sell pressure"
            ),
            evidence=dict(ev),
        )
    ]


def abnormal_volume(bar: Candle, ev: dict[str, Any], pair: str) -> list[Signal]:
    """A volume bar far outside its own trailing distribution, either direction."""
    ratio = ev["volume_ratio"]
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
            severity=severity(conf),
            as_of=bar.timestamp,
            pair=pair,
            summary=(
                f"volume {direction}: {ratio:.2f}x its 50-bar mean "
                f"({'buy' if bar.is_bullish else 'sell'} bar)"
            ),
            evidence=dict(ev),
        )
    ]


def dump_pattern(
    bar: Candle,
    ev: dict[str, Any],
    pair: str,
    *,
    closes: list[float],
) -> list[Signal]:
    """A sharp, oversold down move of at least 1.5x ATR."""
    i = len(closes) - 1
    if i < 1:
        return []
    change = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] else 0.0
    rsi_v, atr_v = ev["rsi14"], ev["atr14"]
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
            severity=severity(conf),
            as_of=bar.timestamp,
            pair=pair,
            summary=(
                f"close {change:.1%} on the bar, RSI {rsi_v:.0f}, "
                f"{move_vs_atr:.1f}x ATR — distribution / liquidation shape"
            ),
            evidence={
                **ev,
                "bar_change": round(change, 6),
                "move_atr_multiple": round(move_vs_atr, 3),
            },
        )
    ]


__all__ = [
    "DRAWDOWN_ALERT",
    "MIN_CONFIDENCE",
    "VOLUME_COLLAPSE_RATIO",
    "VOLUME_DRY_RATIO",
    "Signal",
    "abnormal_volume",
    "dump_pattern",
    "liquidity_collapse",
    "severity",
]
