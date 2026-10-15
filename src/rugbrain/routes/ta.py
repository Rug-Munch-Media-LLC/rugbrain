"""Technical-analysis routes — the tool surface over :mod:`rugbrain.ta`.

The TA substrate (indicators, signatures, backtest, OHLCV reader) shipped with
93 tests but had **no tool surface**: nothing in the HTTP API or the MCP facade
could reach it. This module is that surface — one endpoint that reads real
hourly candles for a DEX pair and returns the evaluated signals.

Read-only: the reader issues exactly one SELECT and this route adds no writes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from rugbrain.ta.reader import CandleReadError, PairRef, fetch_candles, table_stats
from rugbrain.ta.signals import evaluate, trend_state

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["technical-analysis"])

#: Default lookback window (hours) — one week of hourly candles.
DEFAULT_HOURS = 168
#: Hard ceiling mirrors the reader's MAX_WINDOW_HOURS so a typo cannot scan the table.
MAX_HOURS = 8760


def _signal_to_dict(sig: Any) -> dict[str, Any]:
    """Serialise one :class:`~rugbrain.ta.signatures.Signal` for JSON."""
    return {
        "kind": sig.kind,
        "confidence": round(sig.confidence, 4),
        "severity": sig.severity,
        "as_of": sig.as_of.isoformat(),
        "pair": sig.pair,
        "summary": sig.summary,
        "evidence": sig.evidence,
    }


@router.get("/ta/signals")
async def ta_signals(
    chain: str = Query(..., description="Chain id, e.g. 'solana' or 'ethereum'."),
    pair: str = Query(..., description="DEX pair / pool address."),
    hours: int = Query(DEFAULT_HOURS, ge=24, le=MAX_HOURS, description="Lookback window in hours."),
) -> dict[str, Any]:
    """Evaluate the TA signatures for one DEX pair at the latest candle.

    Returns the actionable signals (liquidity collapse, abnormal volume, dump
    pattern) plus the trend state. A pair with too few candles returns an empty
    signal list rather than an error — absence of a signal is not a failure.
    """
    ref = PairRef(chain=chain, pair_address=pair)
    try:
        series = fetch_candles(ref, hours=hours)
    except CandleReadError as exc:
        logger.warning("ta_signals_read_failed pair=%s err=%s", ref, str(exc)[:120])
        raise HTTPException(status_code=503, detail="OHLCV store unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    signals = evaluate(series) if len(series) else []
    closes = series.closes()
    return {
        "pair": str(ref),
        "candles": len(series),
        "window_hours": hours,
        "trend": trend_state(closes) if closes else "flat",
        "signal_count": len(signals),
        "signals": [_signal_to_dict(s) for s in signals],
    }


@router.get("/ta/stats")
async def ta_stats() -> dict[str, Any]:
    """Coverage + freshness of the OHLCV substrate (rows, pairs, latest hour)."""
    try:
        return table_stats()
    except CandleReadError as exc:
        raise HTTPException(status_code=503, detail="OHLCV store unavailable") from exc
