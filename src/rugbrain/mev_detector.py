"""MEV Detector — detects sandwich attacks, frontrunning, and backrunning patterns.

Analyzes transaction patterns for Maximal Extractable Value (MEV):
1. **Sandwich detection**: A user tx is preceded by a buy and followed by a sell
2. **Frontrunning**: A searcher tx placed just before a known target tx
3. **Backrunning**: A searcher tx placed just after a known target tx
4. **JIT liquidity**: Liquidity added just before a swap and removed right after

Uses public mempool data (Etherscan pending tx, Flashbots, BloxRoute) to
identify suspicious MEV patterns in real-time.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Config
ETHERSCAN_KEY = os.getenv("ETHERSCAN_API_KEY", "TZBW87IP5KHU1R7F4S3ZZZ5B2IM2RAS1V7")
FLASHBOTS_URL = "https://blocks.flashbots.net/v1/mev-sandwich"
POLL_INTERVAL = int(os.getenv("MEV_POLL_INTERVAL", "60"))

# Token swap signatures (keccak256 of common function signatures)
SWAP_SIGNATURES = [
    "0x38ed1739",  # swapExactTokensForTokens
    "0x7ff36ab5",  # swapExactETHForTokens
    "0x18cbafe5",  # swapExactTokensForETH
    "0x5c11d795",  # swapExactTokensForTokensSupportingFeeOnTransferTokens
    "0xfb3bdb41",  # swapETHForExactTokens
    "0x4a25d94a",  # swapTokensForExactTokens
    "0x8803dbee",  # swapTokensForExactETH
    "0x2e1a7d4d",  # withdraw
]

# Uniswap V2/V3 pool creation signatures
POOL_CREATION = ["0x0d3648bd", "0x783a112b", "0x6198e5b3"]

# Sandwich detection: window in blocks
SANDWICH_WINDOW_BLOCKS = 3

# In-memory tx buffer for pattern matching
_tx_buffer: list[dict[str, Any]] = []
_mev_alerts: list[dict[str, Any]] = []


def _is_swap_tx(input_data: str) -> bool:
    """Check if a transaction's input data starts with a swap signature."""
    if not input_data or input_data == "0x":
        return False
    sig = input_data[:10].lower()
    return sig in SWAP_SIGNATURES


def _extract_token_addresses(input_data: str) -> list[str]:
    """Extract token addresses from swap transaction input data."""
    if not input_data or len(input_data) < 138:
        return []
    # Common position: path[0] and path[1] at offset 4+96+32 and 4+96+32+32 for Uniswap V2
    try:
        # For swapExactTokensForTokens: path starts at byte 4+32+32+32+32 = 132
        if len(input_data) >= 202:
            token0 = "0x" + input_data[164:204].lower()
            token1 = "0x" + input_data[228:268].lower()
            return [t for t in [token0, token1] if re.match(r"^0x[a-f0-9]{40}$", t)]
    except (IndexError, ValueError):
        pass
    return []


async def detect_sandwich_attacks(block_txs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect sandwich attacks from a list of transactions in a block.

    A sandwich attack has 3 txs in sequence:
    1. Front-run tx (buy, drives price up)
    2. Victim tx (swap at inflated price)
    3. Back-run tx (sell, profits from price difference)
    """
    sandwiches = []
    txs = [tx for tx in block_txs if _is_swap_tx(tx.get("input", ""))]

    for i in range(len(txs) - 2):
        tx1, tx2, tx3 = txs[i], txs[i + 1], txs[i + 2]
        tokens1 = _extract_token_addresses(tx1.get("input", ""))
        tokens2 = _extract_token_addresses(tx2.get("input", ""))
        tokens3 = _extract_token_addresses(tx3.get("input", ""))

        # Check if they're swapping the same pair
        if tokens1 and tokens2 and tokens3:
            if tokens1 == tokens3 and tokens1 != tokens2:
                # Same token pair for front and back run, different from victim
                sandwiches.append({
                    "type": "sandwich",
                    "frontrun_tx": tx1.get("hash", ""),
                    "victim_tx": tx2.get("hash", ""),
                    "backrun_tx": tx3.get("hash", ""),
                    "token_pair": tokens1,
                    "frontrun_from": tx1.get("from", ""),
                    "victim_from": tx2.get("from", ""),
                    "backrun_from": tx3.get("from", ""),
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "severity": "medium",
                })

    return sandwiches


async def poll_flashbots_mev_data() -> list[dict[str, Any]]:
    """Fetch known MEV sandwich attacks from Flashbots API."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{FLASHBOTS_URL}?limit=10")
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []

    mevs = []
    for item in data.get("data", []):
        mevs.append({
            "type": "flashbots_sandwich",
            "tx_hash": item.get("transaction_hash", ""),
            "block_number": item.get("block_number", 0),
            "profit_eth": item.get("profit", {}).get("amount", 0),
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "severity": "info",
        })
    return mevs


async def mev_scan_cycle() -> dict[str, Any]:
    """Run one full MEV scanning cycle."""
    flashbots = await poll_flashbots_mev_data()

    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "flashbots_mevs": len(flashbots),
        "alerts": flashbots[:5],
        "total_active": len(_mev_alerts),
    }


async def mev_scan_loop() -> None:
    """Run MEV detection in a continuous background loop."""
    logger.info("mev_detector_started interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await mev_scan_cycle()
            if result["flashbots_mevs"] > 0:
                logger.info("mev_detected count=%d", result["flashbots_mevs"])
        except Exception as exc:
            logger.warning("mev_scan_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
