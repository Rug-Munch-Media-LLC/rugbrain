"""Live native-asset pricing (CoinGecko simple-price, TTL-cached, fail-open).

A 120s TTL keeps us far under the free-tier limit while every alert
computes USD at a current price. On fetch error we fall back to the env
constant; if that is 0 too, callers stand down rather than computing
nonsense amounts.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("rugbrain.realtime_alerts")

_PRICE_TTL_S = int(os.getenv("REALTIME_PRICE_TTL_S", "120"))
_price_cache: dict[str, tuple[float, float]] = {}  # symbol -> (price, monotonic_ts)
_COINGECKO_IDS = {"ethereum": "ethereum", "solana": "solana"}


async def _fetch_live_price(symbol: str) -> float:
    """Fetch the current USD price for *symbol* from CoinGecko (no auth).

    Raises on network/parse failure — callers handle the fallback.
    """
    cg_id = _COINGECKO_IDS.get(symbol)
    if not cg_id:
        return 0.0
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
        )
        r.raise_for_status()
        return float(r.json()[cg_id]["usd"])


async def resolve_native_price(symbol: str) -> float:
    """Current USD price for *symbol*: live (TTL-cached) → env fallback.

    Returns 0.0 when both sources are unavailable; callers stand down on 0
    instead of computing nonsense USD amounts.
    """
    from rugbrain import realtime_alerts as _pkg

    now = time.monotonic()
    cached = _price_cache.get(symbol)
    if cached and now - cached[1] < _PRICE_TTL_S:
        return cached[0]
    try:
        price = await _pkg._fetch_live_price(symbol)
    except Exception as e:
        log.debug("live_price_fetch_failed symbol=%s: %s", symbol, str(e)[:80])
        price = 0.0
    if price <= 0:
        price = _pkg.ETH_PRICE_USD if symbol == "ethereum" else _pkg.SOL_PRICE_USD
    if price > 0:
        _price_cache[symbol] = (price, now)
    return price
