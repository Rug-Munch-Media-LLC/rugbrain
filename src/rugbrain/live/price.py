"""live_price — rugmunch-data first, coingecko fallback, reject data older than 120 s."""

from __future__ import annotations

import os
from typing import Any

from .envelope import (
    PRICE_STALE_AFTER_S,
    Envelope,
    age_seconds,
    coerce_ts,
    ok_envelope,
    refuse,
)
from .fetch import ProviderError, get_json
from .mcp import mcp_call

PLATFORMS = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "binance-smart-chain",
    "solana": "solana",
    "arbitrum": "arbitrum-one",
    "polygon": "polygon-pos",
}
_PRICE_KEYS = ("price", "priceUsd", "price_usd", "usdPrice", "usd")
_TS_KEYS = ("timestamp", "last_updated_at", "fetched_at", "time", "ts")


def _rugmunch_url() -> str:
    return os.getenv("LIVE_RUGMUNCH_MCP_URL", "http://127.0.0.1:9105/mcp")


def _coingecko_base() -> str:
    return os.getenv("LIVE_COINGECKO_BASE", "https://api.coingecko.com/api/v3").rstrip("/")


def _deep_find(obj: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj[key] not in (None, ""):
                return obj[key]
        for value in obj.values():
            found = _deep_find(value, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _deep_find(value, keys)
            if found is not None:
                return found
    return None


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _looks_like_address(token: str) -> bool:
    return token.startswith("0x") or (len(token) >= 32 and " " not in token)


async def _from_rugmunch(token: str, chain: str) -> tuple[float | None, Any]:
    url = _rugmunch_url()
    if chain == "solana":
        payload, _ = await mcp_call(url, "sol_getTokenPrice", {"mint": token}, provider="rugmunch-data")
    else:
        payload, _ = await mcp_call(
            url, "dex_token", {"address": token, "chain": chain}, provider="rugmunch-data"
        )
    return _num(_deep_find(payload, _PRICE_KEYS)), coerce_ts(_deep_find(payload, _TS_KEYS))


async def _from_coingecko(token: str, chain: str) -> tuple[float | None, Any]:
    base = _coingecko_base()
    if _looks_like_address(token):
        platform = PLATFORMS.get(chain, chain)
        payload, fetched = await get_json(
            f"{base}/simple/token_price/{platform}",
            provider="coingecko",
            params={
                "contract_addresses": token,
                "vs_currencies": "usd",
                "include_last_updated_at": "true",
            },
        )
        row = next(iter(payload.values()), {}) if isinstance(payload, dict) else {}
        return _num(row.get("usd")), coerce_ts(row.get("last_updated_at")) or fetched
    search, fetched = await get_json(f"{base}/search", provider="coingecko", params={"query": token})
    coins = search.get("coins") if isinstance(search, dict) else None
    if not coins:
        raise ProviderError("coingecko", "unknown_token", f"no coingecko id for {token}")
    coin_id = coins[0].get("id")
    payload, fetched = await get_json(
        f"{base}/simple/price",
        provider="coingecko",
        params={"ids": coin_id, "vs_currencies": "usd", "include_last_updated_at": "true"},
    )
    row = payload.get(coin_id, {}) if isinstance(payload, dict) else {}
    return _num(row.get("usd")), coerce_ts(row.get("last_updated_at")) or fetched


def _finish(provider: str, token: str, chain: str, price: float | None, ts: Any) -> Envelope:
    if price is None:
        return refuse("bad_payload", f"{provider} returned no numeric price", provider)
    fetched_at = ts.isoformat().replace("+00:00", "Z") if ts else None
    age = age_seconds(fetched_at)
    if age is not None and age > PRICE_STALE_AFTER_S:
        return refuse(
            "stale",
            f"{provider} price is {age:.0f}s old (limit {PRICE_STALE_AFTER_S:.0f}s)",
            provider,
            fetched_at,
        )
    data = {"token": token, "chain": chain, "price_usd": price, "age_s": round(age or 0.0, 1)}
    return ok_envelope(provider, data, fetched_at)


async def live_price(token: str, chain: str = "solana") -> Envelope:
    """USD price with source truth. Falls back across providers; never returns a silent blank."""
    token = (token or "").strip()
    chain = (chain or "solana").strip().lower()
    if not token:
        return refuse("bad_request", "token is required", "live_price")
    failures: list[str] = []
    refusal: Envelope | None = None
    for provider, fn in (("rugmunch-data", _from_rugmunch), ("coingecko", _from_coingecko)):
        try:
            price, ts = await fn(token, chain)
        except ProviderError as exc:
            failures.append(f"{exc.provider}:{exc.code}")
            continue
        env = _finish(provider, token, chain, price, ts)
        if env.ok:
            return env
        refusal = env
        failures.append(f"{provider}:{env.error['code'] if env.error else 'unknown'}")
    if refusal is not None:
        return refusal
    return refuse("all_providers_failed", "; ".join(failures) or "no provider answered", "live_price")
