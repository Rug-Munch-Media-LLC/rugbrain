"""Hyperliquid liquidation fetcher."""

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

__all__ = ["get_hyperliquid_liquidations"]


async def get_hyperliquid_liquidations() -> list[dict[str, Any]]:
    """Get liquidation orders from Hyperliquid via monitor import + direct API fallback."""
    events = []
    coins = ["BTC", "ETH", "SOL"]
    try:
        from rugbrain.monitors.hyperliquid_monitor import (
            classify_liquidation,
            fetch_liquidations,
        )

        liqs = await fetch_liquidations(limit=50)
        if liqs:
            for liq in liqs:
                try:
                    cls = classify_liquidation(liq)
                    coin = cls.get("coin", "?")
                    if coin not in coins:
                        coins.append(coin)
                    liq_id = f"hl_{liq.get('coin', '')}_{liq.get('time', '')}"
                    if liq_id not in _seen_liquidations:
                        _seen_liquidations.add(liq_id)
                        usd_value = float(cls.get("usd_value", 0))
                        events.append(
                            {
                                "exchange": "Hyperliquid",
                                "symbol": coin,
                                "side": "LONG" if cls.get("is_long") else "SHORT",
                                "qty": float(cls.get("size", 0)),
                                "price": float(cls.get("price", 0)),
                                "value_usd": round(usd_value, 2),
                                "timestamp": datetime.now(UTC).isoformat(),
                                "severity": classify_severity(usd_value),
                            }
                        )
                except Exception:
                    continue
            return events
    except ImportError:
        logger.debug("hl_monitor_not_available_falling_back_to_direct_api")
    except Exception as exc:
        logger.debug("hl_monitor_import_failed %s", str(exc)[:60])

    for coin in coins:
        try:
            payload = {"type": "liquidationOrders", "param": {"limit": 20}}
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post("https://api.hyperliquid.xyz/info", json=payload)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        for liq in data:
                            liq_coin = liq.get("coin", coin)
                            liq_id = f"hl_direct_{liq_coin}_{liq.get('time', '')}"
                            if liq_id not in _seen_liquidations:
                                _seen_liquidations.add(liq_id)
                                sz = float(liq.get("sz", 0))
                                px = float(liq.get("lidPx", liq.get("px", 0)))
                                usd_value = sz * px
                                events.append(
                                    {
                                        "exchange": "Hyperliquid",
                                        "symbol": liq_coin,
                                        "side": "LONG" if liq.get("side") == "B" else "SHORT",
                                        "qty": sz,
                                        "price": px,
                                        "value_usd": round(usd_value, 2),
                                        "timestamp": datetime.now(UTC).isoformat(),
                                        "severity": classify_severity(usd_value),
                                    }
                                )
            await asyncio.sleep(0.3)
        except Exception as exc:
            logger.debug("hyperliquid_direct_liq_failed coin=%s %s", coin, str(exc)[:60])
    return events
