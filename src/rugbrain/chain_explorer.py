"""Chain Explorer API integration — multi-chain block explorer API support.

Wraps Etherscan-compatible APIs for BSCScan, Polygonscan, Arbiscan, etc.
All use the same API format as Etherscan (free tier, 5 req/s).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Chain → (explorer_url, env_var_for_api_key, default_key)
CHAIN_EXPLORERS: dict[str, tuple[str, str, str]] = {
    "ethereum": (
        "https://api.etherscan.io/api",
        "ETHERSCAN_API_KEY",
        "TZBW87IP5KHU1R7F4S3ZZZ5B2IM2RAS1V7",
    ),
    "bsc": (
        "https://api.bscscan.com/api",
        "BSCSCAN_API_KEY",
        "",
    ),
    "polygon": (
        "https://api.polygonscan.com/api",
        "POLYGONSCAN_API_KEY",
        "",
    ),
    "arbitrum": (
        "https://api.arbiscan.io/api",
        "ARBISCAN_API_KEY",
        "",
    ),
    "avalanche": (
        "https://api.snowtrace.io/api",
        "SNOWTRACE_API_KEY",
        "",
    ),
    "optimism": (
        "https://api-optimistic.etherscan.io/api",
        "OPTIMISTIC_ETHERSCAN_KEY",
        "",
    ),
    "base": (
        "https://api.basescan.org/api",
        "BASESCAN_API_KEY",
        "",
    ),
}

# Rate limiting per chain (5 req/s = 0.2s interval)
_last_call: dict[str, float] = {}
MIN_INTERVAL = 0.25


def get_api_key(chain: str) -> str:
    """Get the API key for a chain, checking env vars with fallback."""
    if chain not in CHAIN_EXPLORERS:
        return ""
    _, env_var, default = CHAIN_EXPLORERS[chain]
    return os.getenv(env_var, default)


async def _rate_limited_call(url: str, chain: str) -> dict[str, Any] | None:
    """Make a rate-limited call to a chain explorer API."""
    global _last_call
    now = time.time()
    since_last = _last_call.get(chain, 0)
    if now - since_last < MIN_INTERVAL:
        await asyncio.sleep(MIN_INTERVAL - (now - since_last))
    _last_call[chain] = time.time()

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers={"User-Agent": "rugbrain/1.0"})
            if r.status_code == 200:
                return r.json()
            return None
    except Exception:
        return None


async def get_pending_tx_count(chain: str = "ethereum") -> int:
    """Get the pending transaction count for a chain."""
    key = get_api_key(chain)
    if not key:
        return 0

    base_url = CHAIN_EXPLORERS.get(chain, [None])[0]
    if not base_url:
        return 0

    url = f"{base_url}?module=proxy&action=eth_getBlockByNumber&tag=pending&boolean=false&apikey={key}"
    data = await _rate_limited_call(url, chain)
    if data and isinstance(data, dict):
        block = data.get("result", {})
        if isinstance(block, dict):
            return len(block.get("transactions", []))
    return 0


async def get_account_tx_count(address: str, chain: str = "ethereum") -> int:
    """Get the transaction count for an address on a chain."""
    key = get_api_key(chain)
    if not key:
        return 0

    base_url = CHAIN_EXPLORERS.get(chain, [None])[0]
    if not base_url:
        return 0

    url = f"{base_url}?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&sort=desc&apikey={key}"
    data = await _rate_limited_call(url, chain)
    if data and isinstance(data, dict):
        result = data.get("result", [])
        if isinstance(result, list):
            return len(result)
    return 0
