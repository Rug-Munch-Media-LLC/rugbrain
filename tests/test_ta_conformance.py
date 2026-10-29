"""Conformance lock for the TA numeric path (TA-Lib 0.8.3 reference).

``eval/ta_conformance.py`` measures; this file freezes what it measured. Two
kinds of assertion, both deliberate:

* **Frozen values** — the exact numbers TA-Lib 0.8.3 returns on the fixture in
  ``eval/ta_conformance.py``. If a TA-Lib upgrade changes any of them, that is
  a real change in what a RugBrain TA answer means, and it must fail here
  rather than surface as a silently different number in a user's answer.
* **Hand-computed anchors** — three values derived by arithmetic in the fixture
  docstring (RSI 59.375, SMA 114.25, ATR 10.0). These prove the frozen table was
  not merely copied from output: the arithmetic is checkable without a library.

The Pine deltas are asserted as *relationships*, never as parity. Where Pine and
TA-Lib seed differently, the delta must shrink toward zero as the seed's
influence decays — that is the statement we can defend, and the one a report
should repeat.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

talib = pytest.importorskip("talib")

_ROOT = Path(__file__).resolve().parents[1]

#: (indicator, bar) -> value TA-Lib 0.8.3 returned when the table was frozen.
FROZEN: dict[tuple[str, int], float] = {
    ("SMA(20)", 19): 114.25,
    ("SMA(20)", 62): 167.375,
    ("EMA(20)", 19): 114.25,
    ("EMA(20)", 62): 159.3104407,
    ("RSI(14)", 14): 59.375,
    ("RSI(14)", 62): 36.60578985,
    ("ATR(14)", 14): 10.0,
    ("ATR(14)", 62): 13.46432102,
    ("BBANDS(20,2).middle", 19): 114.25,
    ("BBANDS(20,2).upper", 19): 132.6687405,
    ("BBANDS(20,2).lower", 19): 95.83125954,
    ("MACD(12,26,9).line", 33): 10.32498834,
    ("MACD(12,26,9).line", 62): -3.542187219,
    ("MACD(12,26,9).signal", 33): 10.55194101,
    ("MACD(12,26,9).signal", 62): 5.165956246,
    ("MACD(12,26,9).hist", 33): -0.2269526711,
    ("MACD(12,26,9).hist", 62): -8.708143466,
    ("CDLENGULFING", 56): 80.0,
    ("CDLENGULFING", 61): 100.0,
}


def _load_conformance():
    spec = importlib.util.spec_from_file_location(
        "ta_conformance", _ROOT / "eval" / "ta_conformance.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ta_conformance"] = module
    spec.loader.exec_module(module)
    return module


TC = _load_conformance()
ROWS = {(r.indicator, r.bar): r for r in TC.compare()}


@pytest.mark.parametrize(("key", "expected"), sorted(FROZEN.items()))
def test_frozen_talib_values_reproduce(key: tuple[str, int], expected: float) -> None:
    """The shipped TA-Lib version returns exactly the frozen value."""
    row = ROWS[key]
    assert row.talib is not None
    assert row.talib == pytest.approx(expected, abs=1e-6)


def test_hand_computed_anchors() -> None:
    """Values derived by arithmetic in the fixture docstring — no library needed."""
    assert ROWS[("SMA(20)", 19)].talib == pytest.approx(114.25, abs=0)
    assert ROWS[("RSI(14)", 14)].talib == pytest.approx(59.375, abs=1e-12)
    assert ROWS[("ATR(14)", 14)].talib == pytest.approx(10.0, abs=1e-12)


def test_exact_class_agrees_with_pine() -> None:
    """SMA / RSI / BBANDS are the same algorithm in both: no delta above noise."""
    exact = [r for r in ROWS.values() if r.klass == "exact"]
    assert exact, "no exact-class rows — the table lost its control group"
    for row in exact:
        delta = TC._delta(row)
        assert delta is not None and delta <= 1e-9, f"{row.indicator}@{row.bar} delta={delta}"


def test_seed_delta_decays() -> None:
    """Where the seed differs, the difference must shrink with more bars."""
    pairs = [
        ("EMA(20)", 19, 62),
        ("ATR(14)", 14, 62),
        ("MACD(12,26,9).signal", 33, 62),
        ("MACD(12,26,9).line", 33, 62),
    ]
    for name, early, late in pairs:
        first, last = TC._delta(ROWS[(name, early)]), TC._delta(ROWS[(name, late)])
        assert first is not None and last is not None
        assert first > 0.0, f"{name}: no seed delta measured at bar {early}"
        assert last < first / 2.0, f"{name}: delta did not decay ({first} -> {last})"


def test_engulfing_is_measured_not_assumed() -> None:
    """Bar 61 is an engineered bullish engulfing; bar 60 is not."""
    assert ROWS[("CDLENGULFING", 61)].talib == 100.0
    assert ROWS[("CDLENGULFING", 56)].talib == 80.0


def test_table_exit_code_is_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """A drifted ``exact`` row must fail the run — today none do."""
    assert TC.main() == 0
    assert "exact_drift=0" in capsys.readouterr().out
