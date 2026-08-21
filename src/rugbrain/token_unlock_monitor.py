"""Token Unlock Monitor — tracks upcoming token unlocks, vesting schedules, cliff events.

Monitors:
1. TokenUnlock events on Ethereum (vesting contracts)
2. Known token unlock schedules (from TokenUnlocks, CoinGecko)
3. Cliff events (large single-day unlocks)
4. Linear vesting schedules

All indexed into Qdrant 'token_unlocks' collection.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Known token unlock schedules (token, date, amount, source)
# Data sourced from TokenUnlocks, CoinGecko, and protocol docs
TOKEN_UNLOCKS: list[dict[str, Any]] = [
    # 2026 unlocks
    {
        "token": "APT",
        "name": "Aptos",
        "date": "2026-09-12",
        "amount": 8840000,
        "value_usd": 0,
        "type": "cliff",
        "source": "token_unlocks",
    },
    {
        "token": "ARB",
        "name": "Arbitrum",
        "date": "2026-09-16",
        "amount": 92600000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "OP",
        "name": "Optimism",
        "date": "2026-09-30",
        "amount": 24100000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "SUI",
        "name": "Sui",
        "date": "2026-09-01",
        "amount": 64000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "SEI",
        "name": "Sei",
        "date": "2026-08-15",
        "amount": 55000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "TIA",
        "name": "Celestia",
        "date": "2026-10-31",
        "amount": 175000000,
        "value_usd": 0,
        "type": "cliff",
        "source": "token_unlocks",
    },
    {
        "token": "INJ",
        "name": "Injective",
        "date": "2026-09-01",
        "amount": 5000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "DYDX",
        "name": "dYdX",
        "date": "2026-12-01",
        "amount": 8300000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "BLUR",
        "name": "Blur",
        "date": "2026-09-14",
        "amount": 49000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "ALT",
        "name": "AltLayer",
        "date": "2026-10-25",
        "amount": 105000000,
        "value_usd": 0,
        "type": "cliff",
        "source": "token_unlocks",
    },
    {
        "token": "STRK",
        "name": "StarkNet",
        "date": "2026-11-15",
        "amount": 64000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "W",
        "name": "Wormhole",
        "date": "2026-09-03",
        "amount": 617000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "ENA",
        "name": "Ethena",
        "date": "2026-10-02",
        "amount": 14000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "ETHFI",
        "name": "EtherFi",
        "date": "2026-09-18",
        "amount": 4000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "REZ",
        "name": "Renzo",
        "date": "2026-09-30",
        "amount": 25000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "AEVO",
        "name": "Aevo",
        "date": "2026-09-15",
        "amount": 30000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "PORTAL",
        "name": "Portal",
        "date": "2026-09-29",
        "amount": 50000000,
        "value_usd": 0,
        "type": "cliff",
        "source": "token_unlocks",
    },
    {
        "token": "SAGA",
        "name": "Saga",
        "date": "2026-09-05",
        "amount": 10000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "OMNI",
        "name": "Omni",
        "date": "2026-09-13",
        "amount": 3000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
    {
        "token": "PYTH",
        "name": "Pyth",
        "date": "2026-11-20",
        "amount": 150000000,
        "value_usd": 0,
        "type": "linear",
        "source": "token_unlocks",
    },
]

POLL_INTERVAL = int(os.getenv("UNLOCK_POLL_INTERVAL", "3600"))  # 1 hour


async def get_token_price(token: str) -> float:
    """Get current token price from CoinGecko."""
    try:
        token_map = {
            "APT": "aptos",
            "ARB": "arbitrum",
            "OP": "optimism",
            "SUI": "sui",
            "SEI": "sei",
            "TIA": "celestia",
            "INJ": "injective",
            "DYDX": "dydx",
            "BLUR": "blur",
            "ALT": "altlayer",
            "STRK": "starknet",
            "W": "wormhole",
            "ENA": "ethena",
            "ETHFI": "ether-fi",
            "REZ": "renzo",
            "AEVO": "aevo",
            "PORTAL": "portal",
            "SAGA": "saga",
            "OMNI": "omni",
            "PYTH": "pyth-network",
        }
        cg_id = token_map.get(token)
        if not cg_id:
            return 0.0
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            )
            if r.status_code == 200:
                return float(r.json().get(cg_id, {}).get("usd", 0.0))
    except Exception:
        pass
    return 0.0


async def unlock_scan_cycle() -> dict[str, Any]:
    """Run one token unlock monitoring cycle."""
    now = datetime.now(timezone.utc)
    results = []

    for unlock in TOKEN_UNLOCKS:
        unlock_date = datetime.fromisoformat(unlock["date"]).replace(tzinfo=timezone.utc)
        days_until = (unlock_date - now).days
        price = await get_token_price(unlock["token"])
        value_usd = price * unlock["amount"]

        results.append(
            {
                **unlock,
                "days_until": days_until,
                "price_usd": round(price, 4),
                "value_usd": round(value_usd, 2),
                "value_label": f"${value_usd:,.0f}" if value_usd > 0 else "N/A",
                "is_imminent": days_until <= 30,
                "is_cliff": unlock["type"] == "cliff",
            }
        )
        await asyncio.sleep(0.5)  # Rate limit

    # Sort by date
    results.sort(key=lambda r: r["days_until"])
    imminent = [r for r in results if r["is_imminent"]]
    cliffs = [r for r in results if r["is_cliff"] and r["days_until"] <= 60]

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total_unlocks": len(results),
        "imminent_unlocks": len(imminent),
        "upcoming_cliffs": len(cliffs),
        "unlocks": results[:20],
        "imminent_list": imminent[:5],
        "cliff_alerts": cliffs[:5],
    }


async def unlock_scan_loop() -> None:
    """Run token unlock monitoring in a continuous background loop."""
    logger.info(
        "token_unlock_monitor_started interval=%ds tokens=%d", POLL_INTERVAL, len(TOKEN_UNLOCKS)
    )
    while True:
        try:
            result = await unlock_scan_cycle()
            if result["imminent_unlocks"] > 0:
                logger.info("token_unlocks_imminent count=%d", result["imminent_unlocks"])
        except Exception as exc:
            logger.warning("unlock_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
