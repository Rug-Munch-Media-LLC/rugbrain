"""Indicator request specs — ``"rsi:14"`` in, a validated spec and a bar floor out.

One job: turn the caller's indicator strings into typed
:class:`IndicatorSpec` objects and answer the one question the numeric path
must never guess — **how many bars does this indicator need before its first
value is real?**

That number is the guard: a 200-period SMA computed over 12 bars is a lie, so
:mod:`rugbrain.ta.compute` refuses the request instead of emitting a `None`
tail and calling it an answer. Every requirement here is the length of the
warm-up the reference implementation (TA-Lib 0.8.3) actually consumes, not a
rule of thumb.

Accepted spellings (case-insensitive, ``:`` or ``,`` as the separator)::

    "rsi"            -> rsi(14)          needs 15 closes
    "rsi:21"         -> rsi(21)          needs 22 closes
    "macd:12:26:9"   -> macd(12,26,9)    needs 34 closes (slow + signal - 1)
    "bbands:20:2"    -> bbands(20,2)     needs 20 closes
    "cdl_engulfing"  -> cdl_engulfing    needs 10 bars (TA-Lib candle averages)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

#: Parameter defaults, matching the reference implementation's own defaults.
DEFAULT_PARAMS: Final[dict[str, tuple[float, ...]]] = {
    "sma": (20.0,),
    "ema": (20.0,),
    "rsi": (14.0,),
    "atr": (14.0,),
    "bbands": (20.0, 2.0),
    "macd": (12.0, 26.0, 9.0),
}

#: Candlestick patterns we expose. TA-Lib has 61; these are the ones with a
#: plain-English name a report can cite.
CDL_PATTERNS: Final[frozenset[str]] = frozenset(
    {
        "cdl_doji",
        "cdl_engulfing",
        "cdl_hammer",
        "cdl_morningstar",
        "cdl_shootingstar",
    }
)

#: TA-Lib's candlestick functions need a rolling average of the last 10 bars
#: (``CandleAverage`` settings), so no pattern is defined before bar 10.
CDL_BARS: Final[int] = 10


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    """One requested indicator: a name and its numeric parameters."""

    name: str
    params: tuple[float, ...]

    @property
    def label(self) -> str:
        """Stable output key, e.g. ``rsi(14)`` or ``macd(12,26,9)``."""
        if not self.params:
            return self.name
        args = ",".join(f"{p:g}" for p in self.params)
        return f"{self.name}({args})"


def parse_specs(items: Iterable[str] | str) -> tuple[IndicatorSpec, ...]:
    """Parse indicator strings into unique, validated specs.

    Raises ``ValueError`` on an unknown indicator or a non-numeric parameter —
    a typo in an indicator name is a caller bug, and it must not reach the
    numeric path where it could be answered with zeros.
    """
    raw = [items] if isinstance(items, str) else list(items)
    if not raw:
        raise ValueError("at least one indicator is required")
    specs: list[IndicatorSpec] = []
    for text in raw:
        spec = _parse_one(str(text))
        if any(s.name == spec.name and s.params == spec.params for s in specs):
            raise ValueError(f"duplicate indicator request: {spec.label}")
        specs.append(spec)
    return tuple(specs)


def required_bars(spec: IndicatorSpec) -> int:
    """Bars the reference implementation consumes before its first value."""
    if spec.name in CDL_PATTERNS:
        return CDL_BARS
    p = spec.params
    if spec.name == "macd":
        return round(p[1] + p[2]) - 1
    if spec.name in ("rsi", "atr"):
        # RSI: TA-Lib consumes *changes*, so period+1 prices. ATR: TA-Lib never
        # uses bar 0's true range, so period+1 bars.
        return round(p[0]) + 1
    return round(p[0])


def _parse_one(text: str) -> IndicatorSpec:
    parts = [bit for bit in text.strip().lower().replace(",", ":").split(":") if bit != ""]
    if not parts:
        raise ValueError("empty indicator request")
    name = parts[0]
    if name in DEFAULT_PARAMS or name in CDL_PATTERNS:
        defaults = DEFAULT_PARAMS.get(name, ())
        given = tuple(_number(bit, name) for bit in parts[1:])
        if given and len(given) != len(defaults):
            raise ValueError(f"{name} takes {len(defaults)} parameter(s), got {len(given)}")
        return IndicatorSpec(name=name, params=given or defaults)
    raise ValueError(f"unknown indicator: {name}")


def _number(text: str, name: str) -> float:
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{name}: non-numeric parameter {text!r}") from exc


__all__ = [
    "CDL_BARS",
    "CDL_PATTERNS",
    "DEFAULT_PARAMS",
    "IndicatorSpec",
    "parse_specs",
    "required_bars",
]
