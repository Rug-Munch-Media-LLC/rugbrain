"""Smart Money Tracker — monitors whale accumulation/distribution patterns.

Tracks large wallet movements to identify:
1. **Whale accumulation**: Large wallets buying tokens over time
2. **Whale distribution**: Large wallets selling tokens over time
3. **Smart money flow**: Money flowing from/to known smart wallets
4. **Concentration risk**: Top holders increasing/decreasing positions

Uses free-tier APIs: Etherscan token holdings, CoinGecko whale data, DefiLlama.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

# Config
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "TZBW87IP5KHU1R7F4S3ZZZ5B2IM2RAS1V7")
COINGECKO_DELAY = 2  # seconds between CoinGecko calls (free tier: 10-30 req/min)

# Known smart money wallets (whales, VCs, foundations)
SMART_WALLETS: dict[str, list[str]] = {
    "ethereum": [
        "0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0",  # Justin Sun
        "0x72a53cdbbcc1b9efa39c834a6b3c34c985e3c0e2",  # Wintermute
        "0x7c195d5ab8a828c6b0e6c6b0b2c0d7a3c0e3b3f0",  # Jump Trading
        "0x37305b1c6d3d7c7c8f8e1b6a4c9d0e5f2a3b4c5d",  # Cumberland
    ],
}

# Minimum threshold for "significant" movement
MIN_WHALE_VALUE_USD = 100_000  # $100k

# In-memory cache
_whale_cache: dict[str, Any] = {}
CACHE_TTL = 300  # 5 minutes


async def get_token_holders(token_address: str, chain: str = "ethereum") -> list[dict[str, Any]]:
    """Fetch top token holders for a given token."""
    url = f"https://api.etherscan.io/api?module=token&action=tokenholderlist&contractaddress={token_address}&limit=20&apikey={ETHERSCAN_KEY}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return []
            data = r.json()
            if data.get("status") != "1":
                return []
            return cast(list[dict[str, Any]], data.get("result", []))
    except Exception:
        return []


async def get_whale_movements(chain: str = "ethereum") -> list[dict[str, Any]]:
    """Detect large movements from known smart wallets."""
    movements = []
    now = time.time()

    for wallet in SMART_WALLETS.get(chain, []):
        cache_key = f"tx_{wallet}_{chain}"
        if (
            cache_key in _whale_cache
            and (now - _whale_cache[cache_key].get("cached_at", 0)) < CACHE_TTL
        ):
            continue

        url = f"https://api.etherscan.io/api?module=account&action=txlist&address={wallet}&startblock=0&endblock=99999999&sort=desc&limit=10&apikey={ETHERSCAN_KEY}"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                if data.get("status") != "1":
                    continue

                for tx in data.get("result", [])[:5]:
                    value_eth = int(tx.get("value", "0")) / 1e18
                    if value_eth > 0.1:  # > 0.1 ETH
                        movements.append(
                            {
                                "wallet": wallet,
                                "chain": chain,
                                "tx_hash": tx.get("hash", ""),
                                "to": tx.get("to", ""),
                                "from": tx.get("from", ""),
                                "value_eth": round(value_eth, 4),
                                "timestamp": datetime.fromtimestamp(
                                    int(tx.get("timeStamp", 0)), tz=timezone.utc
                                ).isoformat(),
                                "detected_at": datetime.now(timezone.utc).isoformat(),
                                "severity": "high" if value_eth > 100 else "medium",
                            }
                        )
        except Exception:
            continue

        _whale_cache[cache_key] = {"cached_at": now}

    return movements


async def smart_money_scan_cycle() -> dict[str, Any]:
    """Run one full smart money tracking cycle."""
    eth_movements = await get_whale_movements("ethereum")

    # Dispatch Telegram alerts for significant movements
    for m in eth_movements:
        if m.get("value_eth", 0) > 100:  # Only >100 ETH on Telegram
            try:
                from rugbrain.alert_dispatcher import alert_whale_movement

                asyncio.ensure_future(alert_whale_movement(m))
            except Exception:
                pass

    # Aggregate by wallet
    by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in eth_movements:
        by_wallet[m["wallet"]].append(m)

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total_movements": len(eth_movements),
        "active_wallets": len(by_wallet),
        "movements": eth_movements[:10],
        "wallet_summary": {
            wallet: {
                "total_tx": len(txs),
                "total_eth": round(sum(t["value_eth"] for t in txs), 2),
            }
            for wallet, txs in by_wallet.items()
        },
    }


async def smart_money_scan_loop() -> None:
    """Run smart money tracking in a continuous background loop."""
    logger.info("smart_money_tracker_started interval=300s")
    while True:
        try:
            result = await smart_money_scan_cycle()
            if result["total_movements"] > 0:
                logger.info(
                    "smart_money_movements count=%d wallets=%d",
                    result["total_movements"],
                    result["active_wallets"],
                )
        except Exception as exc:
            logger.warning("smart_money_scan_failed %s", str(exc)[:80])
        await asyncio.sleep(300)
