#!/usr/bin/env python3
"""eval/ta_conformance.py — can a RugBrain TA number be defended?

A TA answer that disagrees with the exchange's own chart is worse than no
answer. This harness measures, on one fixed hand-checkable series
(:mod:`ta_fixture`), three things per indicator:

1. what **TA-Lib 0.8.3** returns (the reference implementation we ship),
2. what **TradingView's documented Pine equivalent code** returns
   (:mod:`ta_pine` — ``ta.ema``, ``ta.rma``, ``ta.rsi``, ``ta.macd``,
   ``ta.stdev``, transcribed from the Pine Script reference), and
3. the delta, classified ``exact`` / ``seed`` / ``definitional``.

It does **not** query a live TradingView chart. The Pine column is our
transcription of the published equivalent code, so what is proven is "we differ
from TV's documented algorithm by exactly this much" — not "we rendered the same
pixels as someone's chart". [REVIEW]

Run:  .venv/bin/python eval/ta_conformance.py
Exit: 0 when every ``exact`` row is within float noise, 1 otherwise.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (str(_HERE.parent / "src"), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Below the path bootstrap on purpose: this file runs both as a script and as a
# by-path import from tests/test_ta_conformance.py.
import numpy as np  # noqa: E402
import talib  # noqa: E402
from ta_fixture import fixture  # noqa: E402
from ta_pine import atr, bbands, ema, macd, rsi, sma  # noqa: E402

#: Float noise floor. TA-Lib is C double arithmetic; Pine is float64.
EPS = 1e-9

__all__ = ["EPS", "Row", "compare", "fixture", "main"]


@dataclass(frozen=True, slots=True)
class Row:
    indicator: str
    bar: int
    talib: float | None
    pine: float | None
    klass: str


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
        ("SMA(20)", talib.SMA(cl, 20), sma(closes, 20), "exact", (19, last)),
        ("EMA(20)", talib.EMA(cl, 20), ema(closes, 20), "seed", (19, last)),
        ("RSI(14)", talib.RSI(cl, 14), rsi(closes, 14), "exact", (14, last)),
        ("ATR(14)", talib.ATR(hi, lo, cl, 14), atr(bars, 14), "seed", (14, last)),
    ]
    tup, tmid, tlow = talib.BBANDS(cl, 20, 2.0, 2.0)
    pup, pmid, plow = bbands(closes, 20, 2.0)
    cases += [
        ("BBANDS(20,2).middle", tmid, pmid, "exact", (19, last)),
        ("BBANDS(20,2).upper", tup, pup, "exact", (19, last)),
        ("BBANDS(20,2).lower", tlow, plow, "exact", (19, last)),
    ]
    tline, tsig, thist = talib.MACD(cl, 12, 26, 9)
    pline, psig, phist = macd(closes, 12, 26, 9)
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
