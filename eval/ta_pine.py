"""Pine Script reference transcriptions — the independent side of the table.

Every function here is a transcription of the *documented* Pine equivalent code
(``ta.ema``, ``ta.rma``, ``ta.rsi``, ``ta.atr``, ``ta.macd``, ``ta.sma``) from
the Pine Script v5 reference. It is deliberately independent of TA-Lib: the
conformance harness compares the two, and a shared implementation would make
the comparison vacuous.

What this proves is "we differ from TV's published algorithm by exactly this
much", not "we rendered the same pixels as someone's chart". [REVIEW]
"""

from __future__ import annotations

import itertools

from rugbrain.ta.candles import Candle


def ema(src: list[float], length: int) -> list[float | None]:
    """``ta.ema``: alpha recursion seeded with ``src[0]`` — NOT with an SMA."""
    alpha = 2.0 / (length + 1.0)
    out: list[float | None] = []
    prev: float | None = None
    for i, value in enumerate(src):
        prev = value if prev is None else alpha * value + (1.0 - alpha) * prev
        out.append(prev if i >= length - 1 else None)
    return out


def rma(src: list[float | None], length: int) -> list[float | None]:
    """``ta.rma``: alpha recursion seeded with ``ta.sma(src, length)``.

    A ``None`` anywhere in the seed window makes that ``ta.sma`` na, so the
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


def rsi(src: list[float], length: int) -> list[float | None]:
    """``ta.rsi`` = 100 - 100/(1 + ta.rma(gain)/ta.rma(loss)); bar 0 is na."""
    ups: list[float | None] = [None]
    downs: list[float | None] = [None]
    for i in range(1, len(src)):
        delta = src[i] - src[i - 1]
        ups.append(max(delta, 0.0))
        downs.append(max(-delta, 0.0))
    up, down = rma(ups, length), rma(downs, length)
    out: list[float | None] = []
    for u, d in zip(up, down, strict=True):
        if u is None or d is None:
            out.append(None)
        elif d == 0.0:
            out.append(100.0 if u > 0 else 50.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + u / d))
    return out


def atr(bars: list[Candle], length: int) -> list[float | None]:
    """``ta.atr`` = ``ta.rma(ta.tr(true), length)``; bar 0 TR = high-low."""
    tr: list[float | None] = [bars[0].high - bars[0].low]
    for prev, cur in itertools.pairwise(bars):
        tr.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return rma(tr, length)


def macd(closes: list[float], fast: int, slow: int, signal: int):
    """``ta.macd``: line = ema(fast) - ema(slow); signal = ema(line, signal)."""
    f, s = ema(closes, fast), ema(closes, slow)
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


def bbands(closes: list[float], length: int, ndev: float):
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


def sma(values: list[float], length: int) -> list[float | None]:
    """``ta.sma`` — the arithmetic mean, na before the window fills."""
    out: list[float | None] = [None] * len(values)
    for i in range(length - 1, len(values)):
        out[i] = sum(values[i - length + 1 : i + 1]) / length
    return out
