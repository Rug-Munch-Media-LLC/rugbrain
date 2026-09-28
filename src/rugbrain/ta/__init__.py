"""Technical analysis over real OHLCV series.

Mission job 6 (``technical_analysis``) was scored at **0.000 gold** because the
repo had no price-series substrate at all: no OHLCV store reader, no indicators,
no signal evaluation, no backtest. The data already existed — 2,596,363 hourly
candles across 53,935 DEX pairs in ``wallet_memory.dex_ohlcv_hourly`` — but
nothing in RugBrain could read it.

This package is that substrate. Layout (one job per module, all under 200 lines):

* :mod:`candles`  — the :class:`Candle` value type and series helpers.
* :mod:`reader`  — ClickHouse reader for ``wallet_memory.dex_ohlcv_hourly``.
* :mod:`indicators` — SMA / EMA / RSI / ATR / volume / volatility / drawdown.
* :mod:`signals` — candles + indicators -> a scored finding.
* :mod:`backtest` — point-in-time evaluator that cannot read the future.

Every indicator is a **pure function over a list of floats** — no pandas, no
numpy dependency in the maths — and every one of them is *causal by
construction*: the value at index ``i`` is computed from ``series[:i+1]`` only.
:mod:`backtest` proves it rather than asserting it (see
``tests/test_ta_no_lookahead.py``).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
