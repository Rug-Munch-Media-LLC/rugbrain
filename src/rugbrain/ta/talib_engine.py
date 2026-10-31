"""TA-Lib 0.8.3 dispatch — the only module that touches the C library.

One job: take validated :class:`~rugbrain.ta.specs.IndicatorSpec` objects plus a
candle series and return one ``float | None`` list per output, aligned 1:1 with
the input bars.

**Why TA-Lib and not our own arithmetic.** ``rugbrain.ta.indicators`` already
implements RSI/EMA/ATR by hand, and its seeding choices are reasonable — but a
number a user compares against an exchange chart has to come from the reference
implementation, not from ours. TA-Lib is that reference: BSD-2-Clause, a
``cp312`` manylinux wheel since 0.8.3, numpy as its only dependency.

**Alignment is the contract.** TA-Lib returns an array the same length as its
input, with ``NaN`` during warm-up. We convert ``NaN`` to ``None`` and hand the
array back untouched — no trimming, no realignment. That is what lets a value be
zipped straight onto its bar, so one number can never be attributed to the wrong
candle.

The library is imported lazily (the fleet convention in
:mod:`rugbrain.ta.reader`): this module imports on a host without TA-Lib and
raises :class:`TalibUnavailable` only when a computation is actually asked for.
"""

from __future__ import annotations

import importlib
import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from .candles import Candle
from .specs import CDL_PATTERNS, IndicatorSpec

Series = list[float | None]

#: Adapters keyed by indicator name. Each takes (talib, params, o, h, l, c) and
#: returns ``{output_suffix: ndarray}`` — the suffix is appended to the spec label
#: so ``macd(12,26,9)`` yields ``macd(12,26,9)``, ``.signal`` and ``.hist``.
_ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "sma": lambda t, p, o, h, lo, c: {"": t.SMA(c, int(p[0]))},
    "ema": lambda t, p, o, h, lo, c: {"": t.EMA(c, int(p[0]))},
    "rsi": lambda t, p, o, h, lo, c: {"": t.RSI(c, int(p[0]))},
    "atr": lambda t, p, o, h, lo, c: {"": t.ATR(h, lo, c, int(p[0]))},
    "bbands": lambda t, p, o, h, lo, c: dict(
        zip((".upper", ".middle", ".lower"), t.BBANDS(c, int(p[0]), p[1], p[1]), strict=True)
    ),
    "macd": lambda t, p, o, h, lo, c: dict(
        zip(("", ".signal", ".hist"), t.MACD(c, int(p[0]), int(p[1]), int(p[2])), strict=True)
    ),
}


class TalibUnavailable(RuntimeError):
    """TA-Lib is not importable on this host — never degraded to our own maths."""


def run(specs: Sequence[IndicatorSpec], candles: Sequence[Candle]) -> dict[str, Series]:
    """Compute every spec over *candles*; one aligned series per output.

    The caller is responsible for having checked :func:`~rugbrain.ta.specs.
    required_bars` — this function will happily return a warm-up-filled array,
    because hiding that is the job of the guard, not of the arithmetic.
    """
    talib = _talib()
    opens, highs, lows, closes = _ohlc(candles)
    out: dict[str, Series] = {}
    for spec in specs:
        adapter = _adapter(talib, spec)
        for suffix, values in adapter(talib, spec.params, opens, highs, lows, closes).items():
            out[spec.label + suffix] = _to_series(values)
    return out


def talib_version() -> str:
    """The loaded TA-Lib version, for a report to cite."""
    return str(_talib().__version__)


def _adapter(talib: Any, spec: IndicatorSpec) -> Callable[..., dict[str, Any]]:
    if spec.name in CDL_PATTERNS:
        fn = getattr(talib, spec.name.upper(), None)
        if fn is None:  # pragma: no cover - guards a future TA-Lib rename
            raise TalibUnavailable(f"TA-Lib has no function {spec.name.upper()}")
        return lambda _t, _p, o, h, lo, c: {"": fn(o, h, lo, c)}
    return _ADAPTERS[spec.name]


def _ohlc(candles: Sequence[Candle]) -> tuple[Any, Any, Any, Any]:
    """Four contiguous float64 arrays — TA-Lib 0.8.3 rejects plain lists."""

    def col(attr: str) -> Any:
        return np.ascontiguousarray([getattr(c, attr) for c in candles], dtype=np.float64)

    return (col("open"), col("high"), col("low"), col("close"))


def _to_series(values: Any) -> Series:
    """``NaN`` -> ``None``; every other value is a plain float."""
    return [None if math.isnan(v) else float(v) for v in values]


def _talib() -> Any:
    try:
        return importlib.import_module("talib")
    except ImportError as exc:  # pragma: no cover - depends on host
        raise TalibUnavailable(
            "TA-Lib is not installed; `pip install ta-lib==0.8.3` (wheel, no compile)"
        ) from exc


__all__ = ["Series", "TalibUnavailable", "run", "talib_version"]
