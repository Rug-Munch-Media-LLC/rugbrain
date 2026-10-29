"""The one fixed, hand-checkable series the conformance table is measured on.

Its whole value is that the numbers can be re-derived with a pencil. Any change
here invalidates every frozen value in ``tests/test_ta_conformance.py``, which
is why the arithmetic lives beside the bars it produces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rugbrain.ta.candles import Candle

BASE_TS = datetime(2026, 10, 1, tzinfo=UTC)


def fixture() -> list[Candle]:
    """63 bars: 60 from one closed-form rule, then 3 engineered engulfing bars.

    The rule (i = 0..59)::

        close[i] = 100 + 1.5*i + 4*(-1)**i     # +9.5 / -6.5 alternating change
        open[i]  = close[i-1]                  # every bar has a real body
        high     = max(o, c) + 1 ; low = min(o, c) - 1

    Hand-checkable anchors (arithmetic, frozen in ``tests/test_ta_conformance.py``):

    * ``close[0] = 104``, ``close[1] = 97.5``, ``close[2] = 107``
    * SMA(20) at bar 19 = 100 + 1.5*9.5 + 4*0 = **114.25**
    * RSI(14) at bar 14: 7 changes of +9.5, 7 of -6.5 -> RS = 4.75/3.25
      -> **59.375**
    * ATR(14) at bar 14: TA-Lib ignores bar 0's true range and seeds with
      mean(TR[1..14]) = (7*8.5 + 7*11.5)/14 = **10.0**
    """
    bars: list[Candle] = []
    prev = 100.0
    for i in range(60):
        close = round(100.0 + 1.5 * i + 4.0 * (-1) ** i, 4)
        bars.append(_bar(len(bars), prev, max(prev, close) + 1.0, min(prev, close) - 1.0, close))
        prev = close
    # Engineered bullish engulfing on bar 61; bar 62 is the neutral last bar.
    bars.append(_bar(len(bars), 120.0, 121.0, 111.0, 112.0))
    bars.append(_bar(len(bars), 110.5, 121.5, 110.0, 120.5))
    bars.append(_bar(len(bars), 120.5, 121.0, 118.0, 118.5))
    return bars


def _bar(n: int, o: float, h: float, lo: float, c: float) -> Candle:
    return Candle(
        timestamp=BASE_TS + timedelta(hours=n),
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=1000.0 + 25.0 * n,
    )
