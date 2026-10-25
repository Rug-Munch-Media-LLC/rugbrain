"""``GET /api/v1/ta/indicators`` — the number a trader actually asks for.

One endpoint, one job: read real hourly candles for a DEX pair and return the
last-bar indicators (RSI-14, ATR-14, trend, volume ratio, drawdown from peak)
together with the timestamps of the candles they were computed from, so a caller
can never mistake a stale bar for a live one.

Read-only: one SELECT through :func:`rugbrain.ta.reader.fetch_candles`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from rugbrain.ta.candles import Series
from rugbrain.ta.indicators import atr, drawdown, rsi, sma
from rugbrain.ta.reader import CandleReadError, PairRef, fetch_candles
from rugbrain.ta.signals import trend_state

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["technical-analysis"])

DEFAULT_HOURS = 168
MAX_HOURS = 8760
#: Volume is "abnormal" at >= 2x its 20-candle baseline (the signal's own bar).
ABNORMAL_VOLUME_RATIO = 2.0


def indicator_payload(series: Series, hours: int) -> dict[str, Any]:
    """Compute the last-bar indicator block for *series*.

    Returns ``None``-valued fields (never fabricated numbers) when the window is
    too short for an indicator — a caller must be able to tell "no value" from
    "value of zero".
    """
    closes, highs, lows, volumes = (
        series.closes(),
        series.highs(),
        series.lows(),
        series.volumes(),
    )
    last = series.last
    atr14 = atr(highs, lows, closes, 14)[-1]
    vol_sma = sma(volumes, 20)[-1]
    peak = max(closes)
    peak_idx = closes.index(peak)
    ratio = (volumes[-1] / vol_sma) if vol_sma else None
    return {
        "pair": f"{last.chain}:{last.pair_address}",
        "candles": len(series),
        "window_hours": hours,
        "as_of": last.timestamp.isoformat(),
        "first_candle": series[0].timestamp.isoformat(),
        "last_close": last.close,
        "rsi14": rsi(closes, 14)[-1],
        "trend": trend_state(closes),
        "atr14": atr14,
        "atr_pct": (atr14 / last.close * 100.0) if atr14 and last.close else None,
        "volume_ratio": ratio,
        "volume_abnormal": (ratio >= ABNORMAL_VOLUME_RATIO) if ratio is not None else None,
        "drawdown_from_peak": drawdown(closes)[-1],
        "peak_close": peak,
        "peak_ts": series[peak_idx].timestamp.isoformat(),
    }


@router.get("/api/v1/ta/indicators")
async def ta_indicators(
    chain: str = Query(..., description="Chain id, e.g. 'solana' or 'ethereum'."),
    pair: str = Query(..., description="DEX pair / pool address."),
    hours: int = Query(DEFAULT_HOURS, ge=24, le=MAX_HOURS, description="Lookback window in hours."),
) -> dict[str, Any]:
    """Last-bar TA indicators for one DEX pair, computed from stored candles."""
    ref = PairRef(chain=chain, pair_address=pair)
    try:
        series = fetch_candles(ref, hours=hours)
    except CandleReadError as exc:
        logger.warning("ta_indicators_read_failed pair=%s err=%s", ref, str(exc)[:120])
        raise HTTPException(status_code=503, detail="OHLCV store unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not len(series):
        raise HTTPException(
            status_code=404,
            detail=f"no OHLCV candles for {ref} in the last {hours}h",
        )
    return indicator_payload(series, hours)
