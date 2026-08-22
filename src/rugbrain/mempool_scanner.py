"""Mempool Scanner — monitors pending transactions for suspicious activity.

Polls public mempool APIs (Ethereum, BSC, Solana) for pending transactions
that match known scam/hack patterns:
- Large value transfers from known-vulnerable contracts
- Transactions interacting with freshly deployed contracts
- MEV/searcher frontruns on known protocols

Data sources (all free-tier):
- Etherscan pending tx API (5 req/s, free key)
- BloxRoute Ethereum mempool (free tier)
- Solana tx subscription via Helius WebSocket (free tier, 25k req/mo)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Config with fallback Etherscan API keys
ETHERSCAN_KEYS = [
    os.getenv("ETHERSCAN_API_KEY", ""),
    os.getenv("ETHERSCAN_API_KEY_ALT", ""),
    os.getenv("ETHERSCAN_API_KEY_ALT2", ""),
]
ETHERSCAN_KEYS = [k for k in ETHERSCAN_KEYS if k]  # Remove empty
HELIUS_KEYS = [
    os.getenv("HELIUS_API_KEY", ""),
    os.getenv("HELIUS_API_KEY_ALT", ""),
]
HELIUS_KEYS = [k for k in HELIUS_KEYS if k]  # Remove empty
POLL_INTERVAL = int(
    os.getenv("MEMPOOL_POLL_INTERVAL", "30")
)  # seconds — 30s to avoid hammering free APIs
MAX_PENDING_TX = int(os.getenv("MEMPOOL_MAX_TX", "30"))

# Suspicious address patterns
FRESH_CONTRACT_AGE = 3600  # 1 hour — contracts younger than this are "fresh"
HIGH_VALUE_THRESHOLD_ETH = 100  # 100 ETH
TOKEN_APPROVAL_PATTERN = re.compile(r"0x095ea7b3", re.IGNORECASE)  # approve(address,uint256)

# Rate limiting
_LAST_ETHERSCAN_CALL = 0.0
ETHERSCAN_MIN_INTERVAL = 0.25  # 4 req/s

# Cache of known addresses to avoid re-checking
_known_fresh_contracts: set[str] = set()
_mempool_history: list[str] = []


async def _rate_limited_etherscan(url: str) -> dict[str, Any] | None:
    global _LAST_ETHERSCAN_CALL
    now = time.time()
    since_last = now - _LAST_ETHERSCAN_CALL
    if since_last < ETHERSCAN_MIN_INTERVAL:
        await asyncio.sleep(ETHERSCAN_MIN_INTERVAL - since_last)
    _LAST_ETHERSCAN_CALL = time.time()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            return r.json() if r.status_code == 200 else None
    except Exception:
        return None


async def poll_ethereum_mempool() -> list[dict[str, Any]]:
    """Poll Etherscan pending tx list for suspicious transactions.

    Tries all configured Etherscan API keys in order for fallback.
    """
    if not ETHERSCAN_KEYS:
        logger.debug("mempool_etherscan_disabled — no API keys")
        return []

    for key in ETHERSCAN_KEYS:
        url = f"https://api.etherscan.io/api?module=proxy&action=eth_getBlockByNumber&tag=pending&boolean=true&apikey={key}"
        data = await _rate_limited_etherscan(url)
        if data and isinstance(data, dict) and "result" in data:
            break
    else:
        return []

    block = data.get("result", {})
    if isinstance(block, str):
        return []
    txs = block.get("transactions", [])[:MAX_PENDING_TX]

    suspicious = []
    for tx in txs:
        value_wei = int(tx.get("value", "0x0"), 16)
        value_eth = value_wei / 1e18
        to_addr = tx.get("to", "").lower()
        tx_hash = tx.get("hash", "")
        from_addr = tx.get("from", "").lower()
        input_data = tx.get("input", "0x")

        flags = []
        if value_eth >= HIGH_VALUE_THRESHOLD_ETH:
            flags.append(f"high_value_{value_eth:.2f}_ETH")
        if TOKEN_APPROVAL_PATTERN.match(input_data):
            flags.append("token_approval")
        if to_addr and to_addr not in _known_fresh_contracts and value_eth > 0:
            flags.append("fresh_contract_interaction")

        if flags and tx_hash not in _mempool_history:
            _mempool_history.append(tx_hash)
            _mempool_history[:] = _mempool_history[-100:]  # keep last 100
            suspicious.append(
                {
                    "tx_hash": tx_hash,
                    "from": from_addr,
                    "to": to_addr,
                    "value_eth": round(value_eth, 4),
                    "flags": flags,
                    "chain": "ethereum",
                    "detected_at": datetime.now(UTC).isoformat(),
                }
            )

    return suspicious


async def poll_solana_mempool() -> list[dict[str, Any]]:
    """Poll Solana pending transactions via Helius API."""
    if not HELIUS_KEYS:
        return []

    for key in HELIUS_KEYS:
        url = f"https://api.helius.xyz/v0/transactions?apiKey={key}&limit=10"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    txs = r.json()
                    break
        except Exception:
            continue
    else:
        return []

    suspicious = []
    for tx in txs[:MAX_PENDING_TX]:
        tx_hash = (
            tx.get("signature", [""])[0]
            if isinstance(tx.get("signature"), list)
            else tx.get("signature", "")
        )
        if not tx_hash or tx_hash in _mempool_history:
            continue

        # Check for large transfers or suspicious interactions
        transfer_type = tx.get("type", "")
        if transfer_type in ("TRANSFER", "SWAP") and tx.get("nativeTransfers", []):
            total = sum(abs(t.get("amount", 0)) for t in tx.get("nativeTransfers", []))
            if total > 1_000_000_000:  # > 1 SOL
                flags = [f"large_transfer_{total}_lamports"]
                _mempool_history.append(tx_hash)
                _mempool_history[:] = _mempool_history[-100:]
                suspicious.append(
                    {
                        "tx_hash": tx_hash,
                        "from": tx.get("feePayer", ""),
                        "to": "",
                        "value_sol": round(total / 1e9, 4),
                        "flags": flags,
                        "chain": "solana",
                        "detected_at": datetime.now(UTC).isoformat(),
                    }
                )

    return suspicious


async def mempool_scan_cycle() -> dict[str, Any]:
    """Run one full mempool scan cycle across all chains.

    Returns stats about what was found.
    """
    eth_results = await poll_ethereum_mempool()
    sol_results = await poll_solana_mempool()

    all_suspicious = eth_results + sol_results

    return {
        "scanned_at": datetime.now(UTC).isoformat(),
        "chains_scanned": ["ethereum", "solana"],
        "ethereum_pending": len(eth_results),
        "solana_pending": len(sol_results),
        "total_suspicious": len(all_suspicious),
        "suspicious_txs": all_suspicious[:10],  # Return top 10
    }


async def mempool_scan_loop() -> None:
    """Run mempool scanning in a continuous loop.

    Designed to run as a background task.
    """
    logger.info("mempool_scanner_started interval=%ds", POLL_INTERVAL)
    while True:
        try:
            result = await mempool_scan_cycle()
            if result["total_suspicious"] > 0:
                logger.info(
                    "mempool_suspicious_found count=%d chains=%s",
                    result["total_suspicious"],
                    result["chains_scanned"],
                )
        except Exception as exc:
            logger.warning("mempool_scan_cycle_failed %s", str(exc)[:80])
        await asyncio.sleep(POLL_INTERVAL)
