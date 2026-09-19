"""OKX liquidation fetcher."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.liquidation_heatmap.constants import (
    _seen_liquidations,
    logger,
)
from rugbrain.liquidation_heatmap.price_bands import classify_severity

__all__ = ["get_okx_liquidations"]


async def get_okx_liquidations() -> list[dict[str, Any]]:
    """Get liquidation orders from OKX API (free, no auth)."""
    events = []
    for inst_id in ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]:
        try:
            url = f"https://www.okx.com/api/v5/public/liquidation-orders?instId={inst_id}&state=filled&limit=20"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    for order in data:
                        liq_id = f"okx_{inst_id}_{order.get('ts', '')}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            usd_value = float(order.get("sz", "0")) * float(order.get("px", "0"))
                            events.append(
                                {
                                    "exchange": "OKX",
                                    "symbol": inst_id,
                                    "side": order.get("side", ""),
                                    "qty": float(order.get("sz", "0")),
                                    "price": float(order.get("px", "0")),
                                    "value_usd": round(usd_value, 2),
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "severity": classify_severity(usd_value),
                                }
                            )
        except Exception as exc:
            logger.debug("okx_liq_failed %s", str(exc)[:60])
    return events
