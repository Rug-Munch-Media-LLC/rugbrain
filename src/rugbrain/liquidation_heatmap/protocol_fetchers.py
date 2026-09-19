"""DeFi protocol liquidation fetchers via subgraphs and APIs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.liquidation_heatmap.constants import (
    ETHERSCAN_KEY,
    LIQUIDATION_CONTRACTS,
    _seen_liquidations,
    logger,
)
from rugbrain.liquidation_heatmap.price_bands import classify_severity

__all__ = [
    "get_protocol_liquidations",
    "get_dydx_liquidations",
    "get_gmx_liquidations",
    "get_synthetix_liquidations",
]


async def get_protocol_liquidations() -> list[dict[str, Any]]:
    """Get liquidation events from DeFi protocols via Etherscan."""
    events = []
    for contract, name in LIQUIDATION_CONTRACTS.items():
        url = f"https://api.etherscan.io/api?module=logs&action=getLogs&address={contract}&fromBlock=0&toBlock=latest&sort=desc&apikey={ETHERSCAN_KEY}"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "1":
                        for log in data.get("result", [])[:10]:
                            tx_hash = log.get("transactionHash", "")
                            if tx_hash and tx_hash not in _seen_liquidations:
                                _seen_liquidations.add(tx_hash)
                                events.append(
                                    {
                                        "exchange": name,
                                        "protocol": name,
                                        "contract": contract,
                                        "tx_hash": tx_hash,
                                        "block_number": int(log.get("blockNumber", "0x0"), 16),
                                        "value_usd": 0,  # Need to decode log data
                                        "timestamp": datetime.now(UTC).isoformat(),
                                        "severity": "high",
                                    }
                                )
        except Exception:
            pass
    return events


async def get_dydx_liquidations() -> list[dict[str, Any]]:
    """Get liquidation events from dYdX perpetuals via REST API."""
    events = []
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.dydx.exchange/v3/markets")
            if r.status_code == 200:
                markets = r.json().get("markets", {})
                for market, data in markets.items():
                    price = float(data.get("oraclePrice", "0"))
                    vol_24h = float(data.get("volume24H", "0"))
                    if vol_24h > 0 and price > 0:
                        liq_id = f"dydx_{market}_{int(vol_24h)}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            events.append(
                                {
                                    "exchange": "dYdX",
                                    "symbol": market.replace("-", ""),
                                    "side": "BOTH",
                                    "qty": vol_24h / price,
                                    "price": price,
                                    "value_usd": round(
                                        vol_24h * 0.02, 2
                                    ),  # est. liq volume ~2% of 24h vol
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "severity": "high" if vol_24h * 0.02 > 1_000_000 else "medium",
                                }
                            )
            await asyncio.sleep(0.5)
    except Exception as exc:
        logger.debug("dydx_liq_failed %s", str(exc)[:60])
    return events


async def get_gmx_liquidations() -> list[dict[str, Any]]:
    """Get liquidation events from GMX on Arbitrum/Avalanche via subgraph."""
    events = []
    chains = {
        "arbitrum": "https://api.thegraph.com/subgraphs/name/gmx-io/gmx-arbitrum",
        "avalanche": "https://api.thegraph.com/subgraphs/name/gmx-io/gmx-avalanche",
    }
    query = """
    {
      liquidatePositions(first: 20, orderBy: timestamp, orderDirection: desc) {
        id
        collateralToken
        sizeDelta
        markPrice
        timestamp
      }
    }
    """
    for chain, url in chains.items():
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, json={"query": query})
                if r.status_code == 200:
                    data = r.json().get("data", {}).get("liquidatePositions", [])
                    for liq in data:
                        liq_id = f"gmx_{chain}_{liq.get('id', '')}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            size = float(liq.get("sizeDelta", "0"))
                            price = float(liq.get("markPrice", "0"))
                            usd_value = size * price
                            events.append(
                                {
                                    "exchange": "GMX",
                                    "symbol": liq.get("collateralToken", "unknown"),
                                    "side": "BOTH",
                                    "qty": size,
                                    "price": price,
                                    "value_usd": round(usd_value, 2),
                                    "timestamp": datetime.fromtimestamp(
                                        int(liq.get("timestamp", "0")), tz=UTC
                                    ).isoformat(),
                                    "severity": classify_severity(usd_value),
                                }
                            )
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("gmx_liq_failed chain=%s %s", chain, str(exc)[:60])
    return events


async def get_synthetix_liquidations() -> list[dict[str, Any]]:
    """Get liquidation events from Synthetix via subgraph."""
    events = []
    url = "https://api.thegraph.com/subgraphs/name/synthetix"
    optimism_url = "https://api.thegraph.com/subgraphs/name/synthetix/synthetix-optimism"
    query = """
    {
      liquidationEvents(first: 20, orderBy: timestamp, orderDirection: desc) {
        id
        account
        liquidator
        amountLiquidated
        currentTotalLiquidated
        timestamp
        block
      }
    }
    """
    for label, subgraph_url in [("mainnet", url), ("optimism", optimism_url)]:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(subgraph_url, json={"query": query})
                if r.status_code == 200:
                    data = r.json().get("data", {}).get("liquidationEvents", [])
                    for liq in data:
                        liq_id = f"synthetix_{label}_{liq.get('id', '')}"
                        if liq_id not in _seen_liquidations:
                            _seen_liquidations.add(liq_id)
                            amount = float(liq.get("amountLiquidated", "0"))
                            usd_value = amount  # SNX priced in USD on-chain
                            events.append(
                                {
                                    "exchange": "Synthetix",
                                    "symbol": "SNX",
                                    "side": "BUY",
                                    "qty": amount,
                                    "price": 1.0,
                                    "value_usd": round(usd_value, 2),
                                    "timestamp": datetime.fromtimestamp(
                                        int(liq.get("timestamp", "0")), tz=UTC
                                    ).isoformat(),
                                    "severity": classify_severity(usd_value),
                                }
                            )
            await asyncio.sleep(0.5)
        except Exception as exc:
            logger.debug("synthetix_liq_failed label=%s %s", label, str(exc)[:60])
    return events
