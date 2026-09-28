"""The value types a backtest produces: one trade, and the run that holds them.

Split out of :mod:`rugbrain.ta.backtest` so the *shapes* of the output live apart
from the loop that produces them. Both are plain data: no indicators, no series,
no I/O. ``BacktestResult``'s derived statistics (win rate, mean/total return)
belong here because a consumer of the result needs them without importing the
harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["BacktestResult", "Trade"]


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
