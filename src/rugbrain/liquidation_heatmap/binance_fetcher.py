"""Binance liquidation fetcher."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.liquidation_heatmap.constants import (
    _seen_liquidations,
    logger,
)
from rugbrain.liquidation_heatmap.price_bands import classify_severity

__all__ = ["get_binance_liquidations"]


async def get_binance_liquidations() -> list[dict[str, Any]]:
    """Get all liquidation orders from Binance Futures (free, no auth)."""
    events = []
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        try:
            url = f"https://fapi.binance.com/fapi/v1/allLiquidationOrders?symbol={symbol}&limit=30"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    for order in r.json():
                        liq_id = f"binance_{order.get('symbol', '')}_{order.get('time', '')}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            if len(_seen_liquidations) > 200000:
                                _seen_liquidations.clear()
                            usd_value = float(order.get("executedQty", "0")) * float(
                                order.get("price", "0")
                            )
                            events.append(
                                {
                                    "exchange": "Binance",
                                    "symbol": symbol,
                                    "side": order.get("side", ""),
                                    "type": order.get("type", ""),
                                    "qty": float(order.get("executedQty", "0")),
                                    "price": float(order.get("price", "0")),
                                    "value_usd": round(usd_value, 2),
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "severity": classify_severity(usd_value),
                                }
                            )
        except Exception as exc:
            logger.debug("binance_liq_failed %s", str(exc)[:60])
    return events
