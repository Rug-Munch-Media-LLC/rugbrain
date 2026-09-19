"""Other centralized exchange liquidation fetchers (Kraken, BitMEX, KuCoin, Phemex)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.liquidation_heatmap.constants import (
    _seen_liquidations,
    logger,
)
from rugbrain.liquidation_heatmap.price_bands import classify_severity

__all__ = [
    "get_bitmex_liquidations",
    "get_kraken_futures_liquidations",
    "get_kucoin_futures_liquidations",
    "get_phemex_liquidations",
]


async def get_kraken_futures_liquidations() -> list[dict[str, Any]]:
    """Get liquidation orders from Kraken Futures API."""
    events = []
    symbols = ["PI_XBTUSD", "PI_ETHUSD", "PI_SOLUSD"]
    for _symbol in symbols:
        try:
            ticker_url = "https://futures.kraken.com/derivatives/api/v3/tickers"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(ticker_url)
                if r.status_code == 200:
                    tickers = r.json().get("tickers", [])
                    for ticker in tickers:
                        ticker_sym = ticker.get("symbol", "")
                        if ticker_sym in symbols:
                            mark = float(ticker.get("markPrice", "0"))
                            funding = float(ticker.get("fundingRate", "0"))
                            vol_24h = float(ticker.get("volume24h", "0"))
                            liq_estimate = vol_24h * abs(funding) * 10
                            if liq_estimate > 1000:
                                liq_id = f"kraken_{ticker_sym}_{int(datetime.now().timestamp())}"
                                if liq_id not in _seen_liquidations:
                                    _seen_liquidations.add(liq_id)
                                    events.append(
                                        {
                                            "exchange": "Kraken Futures",
                                            "symbol": ticker_sym,
                                            "side": "BOTH",
                                            "qty": liq_estimate / mark if mark > 0 else 0,
                                            "price": mark,
                                            "value_usd": round(liq_estimate, 2),
                                            "timestamp": datetime.now(UTC).isoformat(),
                                            "severity": classify_severity(liq_estimate),
                                        }
                                    )
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("kraken_futures_liq_failed %s", str(exc)[:60])
    return events


async def get_bitmex_liquidations() -> list[dict[str, Any]]:
    """Get public liquidation orders from BitMEX API."""
    events = []
    try:
        url = "https://www.bitmex.com/api/v1/liquidation?count=50&reverse=true"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            if r.status_code == 200:
                data = r.json()
                for liq in data:
                    symbol = liq.get("symbol", "unknown")
                    side = liq.get("side", "")
                    qty = abs(float(liq.get("leavesQty", "0")))
                    price = float(liq.get("price", "0"))
                    usd_value = qty * price
                    liq_id = f"bitmex_{liq.get('symbol', '')}_{liq.get('timestamp', '')}"
                    if liq_id not in _seen_liquidations:
                        _seen_liquidations.add(liq_id)
                        events.append(
                            {
                                "exchange": "BitMEX",
                                "symbol": symbol,
                                "side": side,
                                "qty": qty,
                                "price": price,
                                "value_usd": round(usd_value, 2),
                                "timestamp": liq.get("timestamp", datetime.now(UTC).isoformat()),
                                "severity": classify_severity(usd_value),
                            }
                        )
        await asyncio.sleep(0.5)
    except Exception as exc:
        logger.debug("bitmex_liq_failed %s", str(exc)[:60])
    return events


async def get_kucoin_futures_liquidations() -> list[dict[str, Any]]:
    """Get liquidation orders from KuCoin Futures API (free, no auth)."""
    events = []
    symbols = ["XBTUSDTM", "ETHUSDTM", "SOLUSDTM"]
    for symbol in symbols:
        try:
            url = f"https://api-futures.kucoin.com/api/v1/contracts/liquidation?symbol={symbol}"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    for liq in data:
                        liq_id = f"kucoin_{symbol}_{liq.get('time', '')}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            qty = float(liq.get("size", "0"))
                            price = float(liq.get("price", "0"))
                            usd_value = qty * price
                            events.append(
                                {
                                    "exchange": "KuCoin Futures",
                                    "symbol": symbol,
                                    "side": liq.get("side", ""),
                                    "qty": qty,
                                    "price": price,
                                    "value_usd": round(usd_value, 2),
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "severity": classify_severity(usd_value),
                                }
                            )
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug("kucoin_futures_liq_failed symbol=%s %s", symbol, str(exc)[:60])
    return events


async def get_phemex_liquidations() -> list[dict[str, Any]]:
    """Get liquidation orders from Phemex API (free, no auth)."""
    events = []
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for symbol in symbols:
        try:
            url = f"https://api.phemex.com/md/liquidation?symbol={symbol}"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    data = r.json().get("result", {}).get("liquidation", [])
                    for liq in data:
                        liq_id = f"phemex_{symbol}_{liq.get('timestamp', '')}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            qty = float(liq.get("qty", "0"))
                            price = float(liq.get("price", "0"))
                            usd_value = qty * price
                            events.append(
                                {
                                    "exchange": "Phemex",
                                    "symbol": symbol,
                                    "side": liq.get("side", ""),
                                    "qty": qty,
                                    "price": price,
                                    "value_usd": round(usd_value, 2),
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "severity": classify_severity(usd_value),
                                }
                            )
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug("phemex_liq_failed symbol=%s %s", symbol, str(exc)[:60])
    return events
