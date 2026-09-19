"""Deribit liquidation fetcher."""

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

__all__ = ["get_deribit_liquidations"]


async def get_deribit_liquidations() -> list[dict[str, Any]]:
    """Get BTC/ETH settlements from Deribit (proxy for liquidation events)."""
    events = []
    currencies = ["BTC", "ETH"]
    for currency in currencies:
        try:
            url = f"https://www.deribit.com/api/v2/public/get_last_settlements?currency={currency}&type=delivery&count=20"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    data = r.json().get("result", {}).get("settlements", [])
                    for settlement in data:
                        settlement_type = settlement.get("type", "")
                        if settlement_type in ("delivery", "settlement"):
                            idx = settlement.get("settlement_index", "")
                            liq_id = f"deribit_{currency}_{idx}"
                            if liq_id not in _seen_liquidations:
                                _seen_liquidations.add(liq_id)
                                session_profit = float(settlement.get("session_profit_loss", "0"))
                                usd_value = abs(session_profit)
                                events.append(
                                    {
                                        "exchange": "Deribit",
                                        "symbol": f"{currency}-USD",
                                        "side": "BOTH",
                                        "qty": 0,
                                        "price": float(settlement.get("delivery_price", "0")),
                                        "value_usd": round(usd_value, 2),
                                        "timestamp": datetime.fromtimestamp(
                                            int(settlement.get("timestamp", "0")) / 1000,
                                            tz=UTC,
                                        ).isoformat(),
                                        "severity": classify_severity(usd_value),
                                    }
                                )
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("deribit_liq_failed currency=%s %s", currency, str(exc)[:60])
    return events
