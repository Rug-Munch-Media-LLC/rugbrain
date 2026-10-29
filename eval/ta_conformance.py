#!/usr/bin/env python3
"""eval/ta_conformance.py — can a RugBrain TA number be defended?

A TA answer that disagrees with the exchange's own chart is worse than no
answer. This harness measures, on one fixed hand-checkable series, three things
per indicator:

1. what **TA-Lib 0.8.3** returns (the reference implementation we ship),
2. what **TradingView's documented Pine equivalent code** returns, transcribed
   here from the Pine Script reference (``ta.ema``, ``ta.rma``, ``ta.rsi``,
   ``ta.macd``, ``ta.stdev``), and
3. the delta, classified ``exact`` / ``seed`` / ``definitional``.

It does **not** query a live TradingView chart. The Pine column is our
transcription of the published equivalent code, so what is proven is "we differ
from TV's documented algorithm by exactly this much" — not "we rendered the same
pixels as someone's chart". [REVIEW]

Run:  .venv/bin/python eval/ta_conformance.py
Exit: 0 when every ``exact`` row is within float noise, 1 otherwise.
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import talib

from rugbrain.ta.candles import Candle

#: Float noise floor. TA-Lib is C double arithmetic; Pine is float64.
EPS = 1e-9
BASE_TS = datetime(2026, 10, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Row:
    indicator: str
    bar: int
    talib: float | None
    pine: float | None
    klass: str


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


def pine_ema(src: list[float], length: int) -> list[float | None]:
    """``ta.ema``: alpha recursion seeded with ``src[0]`` — NOT with an SMA."""
    alpha = 2.0 / (length + 1.0)
    out: list[float | None] = []
    prev: float | None = None
    for i, value in enumerate(src):
        prev = value if prev is None else alpha * value + (1.0 - alpha) * prev
        out.append(prev if i >= length - 1 else None)
    return out


def pine_rma(src: list[float | None], length: int) -> list[float | None]:
    """``ta.rma``: alpha recursion seeded with ``ta.sma(src, length)``.

    An ``None`` anywhere in the seed window makes that ``ta.sma`` na, so the
    seed slides to the first full non-na window — that na-propagation is what
    keeps ``ta.rsi`` aligned with TA-Lib's RSI.
    """
    out: list[float | None] = [None] * len(src)
    start = len(src)
    for i in range(length - 1, len(src)):
        window = src[i - length + 1 : i + 1]
        if all(v is not None for v in window):
            start = i
            break
    if start >= len(src):
        return out
    alpha = 1.0 / length
    prev = sum(v for v in src[start - length + 1 : start + 1] if v is not None) / length
    out[start] = prev
    for i in range(start + 1, len(src)):
        value = src[i]
        if value is not None:
            prev = alpha * value + (1.0 - alpha) * prev
            out[i] = prev
    return out


def pine_rsi(src: list[float], length: int) -> list[float | None]:
    """``ta.rsi`` = 100 - 100/(1 + ta.rma(gain)/ta.rma(loss)); bar 0 is na."""
    ups: list[float | None] = [None]
    downs: list[float | None] = [None]
    for i in range(1, len(src)):
        delta = src[i] - src[i - 1]
        ups.append(max(delta, 0.0))
        downs.append(max(-delta, 0.0))
    up, down = pine_rma(ups, length), pine_rma(downs, length)
    out: list[float | None] = []
    for u, d in zip(up, down, strict=True):
        if u is None or d is None:
            out.append(None)
        elif d == 0.0:
            out.append(100.0 if u > 0 else 50.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + u / d))
    return out


def pine_atr(bars: list[Candle], length: int) -> list[float | None]:
    """``ta.atr`` = ``ta.rma(ta.tr(true), length)``; bar 0 TR = high-low."""
    tr: list[float | None] = [bars[0].high - bars[0].low]
    for prev, cur in itertools.pairwise(bars):
        tr.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return pine_rma(tr, length)


def pine_macd(closes: list[float], fast: int, slow: int, signal: int):
    """``ta.macd``: line = ema(fast) - ema(slow); signal = ema(line, signal)."""
    f, s = pine_ema(closes, fast), pine_ema(closes, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(f, s, strict=True)]
    first = next(i for i, v in enumerate(line) if v is not None)
    sig: list[float | None] = [None] * len(line)
    alpha = 2.0 / (signal + 1.0)
    prev = line[first]
    for i in range(first, len(line)):
        val = line[i]
        if val is None:
            continue
        prev = val if i == first else alpha * val + (1.0 - alpha) * prev
        if i >= first + signal - 1:
            sig[i] = prev
    hist = [None if a is None or b is None else a - b for a, b in zip(line, sig, strict=True)]
    return line, sig, hist


def pine_bbands(closes: list[float], length: int, ndev: float):
    """``ta.sma ± ndev * ta.stdev(src, length)`` — population (biased) sigma."""
    up: list[float | None] = [None] * len(closes)
    mid: list[float | None] = [None] * len(closes)
    low: list[float | None] = [None] * len(closes)
    for i in range(length - 1, len(closes)):
        window = closes[i - length + 1 : i + 1]
        mean = sum(window) / length
        sd = (sum((v - mean) ** 2 for v in window) / length) ** 0.5
        mid[i], up[i], low[i] = mean, mean + ndev * sd, mean - ndev * sd
    return up, mid, low


def compare() -> list[Row]:
    """Indicator x (first defined bar, last bar) — the delta table."""
    bars = fixture()
    closes = [b.close for b in bars]
    cols = {
        f: np.ascontiguousarray([getattr(b, f) for b in bars], dtype=np.float64)
        for f in ("open", "high", "low", "close")
    }
    op, hi, lo, cl = cols["open"], cols["high"], cols["low"], cols["close"]
    last = len(bars) - 1
    cases: list[tuple[str, object, object, str, tuple[int, ...]]] = [
        ("SMA(20)", talib.SMA(cl, 20), _sma(closes, 20), "exact", (19, last)),
        ("EMA(20)", talib.EMA(cl, 20), pine_ema(closes, 20), "seed", (19, last)),
        ("RSI(14)", talib.RSI(cl, 14), pine_rsi(closes, 14), "exact", (14, last)),
        ("ATR(14)", talib.ATR(hi, lo, cl, 14), pine_atr(bars, 14), "seed", (14, last)),
    ]
    tup, tmid, tlow = talib.BBANDS(cl, 20, 2.0, 2.0)
    pup, pmid, plow = pine_bbands(closes, 20, 2.0)
    cases += [
        ("BBANDS(20,2).middle", tmid, pmid, "exact", (19, last)),
        ("BBANDS(20,2).upper", tup, pup, "exact", (19, last)),
        ("BBANDS(20,2).lower", tlow, plow, "exact", (19, last)),
    ]
    tline, tsig, thist = talib.MACD(cl, 12, 26, 9)
    pline, psig, phist = pine_macd(closes, 12, 26, 9)
    cases += [
        ("MACD(12,26,9).line", tline, pline, "seed", (33, last)),
        ("MACD(12,26,9).signal", tsig, psig, "seed", (33, last)),
        ("MACD(12,26,9).hist", thist, phist, "seed", (33, last)),
        (
            "CDLENGULFING",
            talib.CDLENGULFING(op, hi, lo, cl),
            [None] * len(bars),
            "definitional",
            (56, 61),
        ),
    ]
    rows: list[Row] = []
    for name, tv, pv, klass, at in cases:
        for idx in at:
            rows.append(Row(name, idx, _clean(tv[idx]), _clean(pv[idx]), klass))
    return rows


def _sma(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(length - 1, len(values)):
        out[i] = sum(values[i - length + 1 : i + 1]) / length
    return out


def _clean(value: object) -> float | None:
    if value is None:
        return None
    val = float(value)  # type: ignore[arg-type]
    return None if val != val else val  # NaN


def _delta(row: Row) -> float | None:
    if row.talib is None or row.pine is None:
        return None
    return abs(row.talib - row.pine)


def main() -> int:
    """Print the conformance table; exit non-zero if an ``exact`` row drifted."""
    rows = compare()
    print(f"# TA conformance — TA-Lib {talib.__version__}\n")
    print("| Indicator | Bar | TA-Lib 0.8.3 | Pine (reimpl.) | \\|Δ\\| | Class |")
    print("|---|---|---|---|---|---|")
    failures = 0
    for r in rows:
        d = _delta(r)
        if r.klass == "exact" and d is not None and d > EPS:
            failures += 1
        print(
            f"| {r.indicator} | {r.bar} | {_fmt(r.talib)} | {_fmt(r.pine)} | {_fmt(d)} | {r.klass} |"
        )
    print(f"\nrows={len(rows)} exact_drift={failures}")
    return 1 if failures else 0


def _fmt(value: float | None) -> str:
    if value is None:
        return "na"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:.10g}"


if __name__ == "__main__":
    raise SystemExit(main())
