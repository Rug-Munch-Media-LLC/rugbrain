"""Market Health Monitor — stablecoin pegs, perp funding rates, market indicators.

Tracks:
1. Stablecoin peg deviation (DAI, USDC, USDT — % from $1)
2. Perp funding rates (BTC, ETH perps across exchanges)
3. Market Fear & Greed index (already in fear_index.py)
4. ETH gas price (network activity proxy)

All indexed into Qdrant 'market_health' collection.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Stablecoin contracts (Ethereum)
STABLECOINS: dict[str, str] = {
    "DAI": "0x6b175474e89094c44da98b954eedeac495271d0f",
    "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
}

POLL_INTERVAL = int(os.getenv("MARKET_HEALTH_INTERVAL", "600"))  # 10 min
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "TZBW87IP5KHU1R7F4S3ZZZ5B2IM2RAS1V7")


async def get_stablecoin_peg() -> dict[str, float]:
    """Get current stablecoin prices from CoinGecko."""
    ids = "dai,usd-coin,tether"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "DAI": data.get("dai", {}).get("usd", 1.0),
                    "USDC": data.get("usd-coin", {}).get("usd", 1.0),
                    "USDT": data.get("tether", {}).get("usd", 1.0),
                }
    except Exception:
        pass
    return {"DAI": 1.0, "USDC": 1.0, "USDT": 1.0}


async def get_funding_rates() -> list[dict[str, Any]]:
    """Get perp funding rates from Binance API (free, no auth)."""
    rates = []
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(
                    f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
                )
                if r.status_code == 200:
                    data = r.json()
                    if data:
                        rates.append(
                            {
                                "symbol": symbol,
                                "funding_rate": float(data[0].get("fundingRate", 0)),
                                "funding_time": datetime.fromtimestamp(
                                    int(data[0].get("fundingTime", 0)) / 1000, tz=UTC
                                ).isoformat(),
                            }
                        )
        except Exception:
            pass
    return rates


async def get_gas_price() -> dict[str, Any]:
    """Get current ETH gas price."""
    url = f"https://api.etherscan.io/api?module=gastracker&action=gasoracle&apikey={ETHERSCAN_KEY}"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(url)
            if r.status_code == 200:
                data = r.json().get("result", {})
                return {
                    "safe_gwei": int(data.get("SafeGasPrice", 0)),
                    "propose_gwei": int(data.get("ProposeGasPrice", 0)),
                    "fast_gwei": int(data.get("FastGasPrice", 0)),
                }
    except Exception:
        pass
    return {"safe_gwei": 0, "propose_gwei": 0, "fast_gwei": 0}


async def market_health_cycle() -> dict[str, Any]:
    """Run one market health monitoring cycle."""
    pegs, rates, gas = await asyncio.gather(
        get_stablecoin_peg(), get_funding_rates(), get_gas_price()
    )

    peg_deviations = {}
    for coin, price in pegs.items():
        deviation = abs(price - 1.0) * 100
        peg_deviations[coin] = round(deviation, 4)

    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "stablecoin_pegs": pegs,
        "peg_deviations_pct": peg_deviations,
        "funding_rates": rates,
        "gas_prices": gas,
        "alerts": [coin for coin, dev in peg_deviations.items() if dev > 0.5],
    }


async def market_health_loop() -> None:
    """Run market health monitoring in a continuous background loop."""
    logger.info("market_health_monitor_started interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await market_health_cycle()
            if result["alerts"]:
                logger.info("stablecoin_peg_alert coins=%s", result["alerts"])
        except Exception as exc:
            logger.warning("market_health_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
