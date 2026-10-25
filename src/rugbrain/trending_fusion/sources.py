"""Live adapters for trending fusion. DexScreener is wired; GMGN, DexTools and
the in-house scanner are NOT yet - they are reported as missing, never as zero.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

from rugbrain.trending_fusion.fuse import fuse, load_weights

SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q={q}"
BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"
PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/{addrs}"
CANDIDATE_CAP = 30
UNWIRED = {
    "gmgn": "no stable public endpoint wired - holder_growth/bonding_progress unavailable",
    "dextools": "promo feed needs a paid API key - DexTools paid placements not collected",
    "scanner": "in-house scanner not called on this path - scanner_score unavailable",
    "social": "RAG social corpus not queried here - social_mentions unavailable",
}


async def _get_json(url: str) -> Any:
    import httpx

    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "rugbrain-trending/1.0"}) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


def _now_ms() -> float:
    """Wall clock in milliseconds (pair timestamps are ms)."""
    return time.time() * 1000.0


def _pair_rows(pairs: Iterable[dict[str, Any]], chain: str) -> list[dict[str, Any]]:
    """Fold DexScreener pairs into one row per token, newest-window signals only."""
    agg: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if (pair.get("chainId") or "") != chain:
            continue
        address = (pair.get("baseToken") or {}).get("address")
        if not address:
            continue
        slot = agg.setdefault(
            address,
            {"symbol": "", "h1": 0.0, "h6": 0.0, "h24": 0.0, "liq": 0.0,
             "buys": 0.0, "sells": 0.0, "change": None, "created": None},
        )
        slot["symbol"] = slot["symbol"] or (pair.get("baseToken") or {}).get("symbol") or ""
        volume = pair.get("volume") or {}
        for window in ("h1", "h6", "h24"):
            slot[window] += float(volume.get(window) or 0.0)
        slot["liq"] += float((pair.get("liquidity") or {}).get("usd") or 0.0)
        txns = (pair.get("txns") or {}).get("h24") or {}
        slot["buys"] += float(txns.get("buys") or 0.0)
        slot["sells"] += float(txns.get("sells") or 0.0)
        change = (pair.get("priceChange") or {}).get("h24")
        if change is not None:
            slot["change"] = max(float(slot["change"] or 0.0), abs(float(change)))
        created = pair.get("pairCreatedAt")
        if created:
            slot["created"] = min(slot["created"] or float(created), float(created))
    ages: list[float] = []
    now_ms = _now_ms()
    for slot in agg.values():
        ages.append(max((now_ms - slot["created"]) / 3_600_000.0, 0.0) if slot["created"] else -1.0)
    known = sorted(a for a in ages if a >= 0.0)
    neutral_age = known[len(known) // 2] if known else 72.0  # unknown age -> median (neutral)
    rows = []
    for address, slot in agg.items():
        if slot["h24"] <= 0:
            continue
        accel = min(slot["h6"] * 4.0 / slot["h24"], 5.0) if slot["h24"] else 0.0
        trades = slot["buys"] + slot["sells"]
        created_h = (now_ms - slot["created"]) / 3_600_000.0 if slot["created"] else neutral_age
        signals = {
            "volume_24h": slot["h24"],
            "volume_accel": accel,
            "liquidity": slot["liq"],
            "growth": trades,
            "momentum_24h": slot["change"] if slot["change"] is not None else 0.0,
            "age": 1.0 / (1.0 + max(created_h, 0.0) / 24.0),
        }
        if trades > 0:
            signals["buy_pressure"] = slot["buys"] / trades
        rows.append(
            {
                "source": "dexscreener",
                "chain": chain,
                "token_address": address,
                "symbol": slot["symbol"],
                "signals": signals,
            }
        )
    return rows


def _boost_rows(payload: Any, chain: str) -> list[dict[str, Any]]:
    items = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(items, dict):
        items = items.get("boosts") or items.get("data") or []
    rows = []
    for item in items or []:
        if (item.get("chainId") or "") != chain:
            continue
        address = item.get("tokenAddress") or item.get("address")
        if not address:
            continue
        amount = float(item.get("totalAmount") or item.get("amount") or 1.0)
        rows.append(
            {
                "source": "dexscreener",
                "chain": chain,
                "token_address": address,
                "symbol": item.get("symbol") or "",
                "signals": {"promotion_paid": amount},
                "paid": True,
                "paid_kind": "dexscreener_boost",
                "paid_amount": amount,
            }
        )
    return rows



def _candidate_addresses(profiles: Any, boosts: Any, chain: str) -> list[str]:
    """Union of recently profiled and paid-boosted tokens for this chain."""
    items = []
    for payload in (profiles, boosts):
        if isinstance(payload, dict):
            payload = payload.get("data") or payload.get("boosts") or []
        if isinstance(payload, list):
            items.extend(payload)
    seen: list[str] = []
    for item in items:
        if not isinstance(item, dict) or (item.get("chainId") or "") != chain:
            continue
        address = item.get("tokenAddress") or item.get("address")
        if address and address not in seen:
            seen.append(address)
    return seen[:CANDIDATE_CAP]


async def trending_fused(
    chain: str = "solana",
    limit: int = 10,
    query: str | None = None,
    scanner_scores: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Fetch what is wired, fuse, and name every source that is not.

    Discovery = DexScreener latest token profiles + paid boosts (cap 30), then
    hydrated through /latest/dex/tokens for real volume/liquidity. GMGN and
    DexTools discovery are not wired, so this is a DexScreener sample, not the
    whole Solana market - stated in ``missing_sources``.
    """
    missing = dict(UNWIRED)
    profiles: Any = []
    boosts: Any = []
    try:
        profiles = await _get_json(PROFILES_URL)
    except Exception as exc:
        missing["dexscreener_profiles"] = f"profile fetch failed: {type(exc).__name__}"
    try:
        boosts = await _get_json(BOOSTS_URL)
    except Exception as exc:
        missing["dexscreener_boosts"] = f"boost fetch failed: {type(exc).__name__}"
    addresses = _candidate_addresses(profiles, boosts, chain)
    pairs: list[dict[str, Any]] = []
    for start in range(0, len(addresses), CANDIDATE_CAP):
        chunk = addresses[start : start + CANDIDATE_CAP]
        try:
            payload = await _get_json(TOKENS_URL.format(addrs=",".join(chunk)))
            pairs.extend(payload.get("pairs") or [])
        except Exception as exc:
            missing["dexscreener_pairs"] = f"hydrate failed: {type(exc).__name__}"
    rows = _pair_rows(pairs, chain) + _boost_rows(boosts, chain)
    if scanner_scores:
        missing.pop("scanner", None)
        for address, risk in scanner_scores.items():
            rows.append(
                {
                    "source": "scanner",
                    "chain": chain,
                    "token_address": address,
                    "signals": {"risk_penalty": max(0.0, min(100.0, float(risk)) / 100.0)},
                }
            )
    missing["discovery_scope"] = (
        f"DexScreener-only candidate sample ({len(addresses)} addresses from latest profiles + boosts)"
    )
    result = fuse(rows, weights=load_weights(), limit=limit, missing_sources=missing)
    result["chain"] = chain
    return result
