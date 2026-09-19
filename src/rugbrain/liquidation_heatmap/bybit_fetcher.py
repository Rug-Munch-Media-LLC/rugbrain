"""Bybit liquidation fetcher."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.liquidation_heatmap.constants import (
    _seen_liquidations,
    logger,
)
from rugbrain.liquidation_heatmap.price_bands import classify_severity

__all__ = ["get_bybit_liquidations"]


async def get_bybit_liquidations() -> list[dict[str, Any]]:
    """Get liquidation orders from Bybit API (free, no auth)."""
    events = []
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        try:
            url = f"https://api.bybit.com/v5/market/liquidation-info?symbol={symbol}&limit=30"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    data = r.json().get("result", {}).get("list", [])
                    for order in data:
                        liq_id = f"bybit_{order.get('symbol', '')}_{order.get('time', '')}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            usd_value = float(order.get("size", "0")) * float(
                                order.get("price", "0")
                            )
                            events.append(
                                {
                                    "exchange": "Bybit",
                                    "symbol": symbol,
                                    "side": order.get("side", ""),
                                    "qty": float(order.get("size", "0")),
                                    "price": float(order.get("price", "0")),
                                    "value_usd": round(usd_value, 2),
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "severity": classify_severity(usd_value),
                                }
                            )
        except Exception as exc:
            logger.debug("bybit_liq_failed %s", str(exc)[:60])
    return events
