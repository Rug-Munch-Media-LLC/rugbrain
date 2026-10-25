"""GeckoTerminal discovery adapter (free, no key).

VERIFIED LIVE 2026-10-25:
  GET https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools?include=base_token&page=1
  Network slugs confirmed against /api/v2/networks (100 ids):
  eth, bsc, base, arbitrum, polygon_pos, optimism, avalanche, solana.
  envelope -> data[] (pool), included[] (type=token)
  pool.attributes  -> address, name, reserve_in_usd, volume_usd{h1,h6,h24},
                      pool_created_at, price_change_percentage{h1,h6,h24},
                      transactions{h1,h6,h24}{buys,sells}
  token.attributes -> address, symbol   (base token of the pool)

FIELD MAP into the shared candidate row (fuse.py contract):
  volume_24h   <- volume_usd.h24                 (sum of the token's pools)
  volume_accel <- min(volume_usd.h6 * 4 / h24, 5) (max of the token's pools)
  liquidity    <- reserve_in_usd                 (sum)
  growth       <- transactions.h24.buys + sells   (sum)
  momentum_24h <- abs(price_change_percentage.h24)
  buy_pressure <- buys / (buys + sells)
  age          <- 1 / (1 + pool_age_hours / 24)   (0.5 when unparseable)
Organic feed only: no paid marker is produced here.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from rugbrain.trending_fusion.chains import gt_networks, resolve_chain

#: Chain -> slug is DERIVED from config/chains.json (chains.gt_networks), never
#: a second hardcoded list. Import happens after the config loader is defined.
NETWORKS: dict[str, str] = gt_networks()
URL = "https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools?include=base_token&page=1"
Fetcher = Callable[[str], Awaitable[Any]]


class UnknownNetwork(ValueError):
    """Chain has no verified GeckoTerminal network slug."""


def resolve_network(chain: str) -> str | None:
    """Map a canonical chain name/alias to its configured GeckoTerminal slug."""
    spec = resolve_chain(chain)
    return spec.gt_slug if spec is not None else None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _age(pool_created_at: Any) -> float:
    text = str(pool_created_at or "").replace("Z", "+00:00")
    try:
        created = datetime.fromisoformat(text)
    except ValueError:
        return 0.5
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    hours = max((datetime.now(UTC) - created).total_seconds() / 3600.0, 0.0)
    return 1.0 / (1.0 + hours / 24.0)


def _pool_signals(attributes: dict[str, Any]) -> dict[str, float]:
    volume = attributes.get("volume_usd") or {}
    h6 = _num(volume.get("h6")) or 0.0
    h24 = _num(volume.get("h24")) or 0.0
    txns = (attributes.get("transactions") or {}).get("h24") or {}
    buys = _num(txns.get("buys")) or 0.0
    sells = _num(txns.get("sells")) or 0.0
    change = (attributes.get("price_change_percentage") or {}).get("h24")
    return {
        "volume_24h": h24,
        "volume_accel": min(h6 * 4.0 / h24, 5.0) if h24 > 0 else 0.0,
        "liquidity": _num(attributes.get("reserve_in_usd")) or 0.0,
        "growth": buys + sells,
        "momentum_24h": abs(_num(change) or 0.0),
        "buy_pressure": (buys / (buys + sells)) if (buys + sells) > 0 else 0.0,
        "age": _age(attributes.get("pool_created_at")),
    }


def _symbols(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in payload.get("included") or []:
        if item.get("type") != "token":
            continue
        attrs = item.get("attributes") or {}
        ident = str(item.get("id") or "")
        address = str(
            attrs.get("address") or (ident.split("_", 1)[1] if "_" in ident else ident)
        ).lower()
        if address:
            out[address] = attrs.get("symbol") or ""
    return out


# Relationship ids are "<network>_<address>"; polygon_pos carries an underscore
# in the network slug, so anchor on the address itself, never the first "_".
_ADDRESS_ID = re.compile(r"(0x[0-9a-fA-F]{40})$")


def _base_address(pool: dict[str, Any]) -> str:
    rel = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
    ident = str(rel.get("id") or "")
    match = _ADDRESS_ID.search(ident)
    if match:
        return match.group(1).lower()
    return (ident.split("_", 1)[1] if "_" in ident else ident).lower()


def rows_from_payload(payload: dict[str, Any], chain: str) -> list[dict[str, Any]]:
    """Fold one trending_pools payload into candidate rows (one per base token)."""
    symbols = _symbols(payload)
    agg: dict[str, dict[str, Any]] = {}
    for pool in payload.get("data") or []:
        address = _base_address(pool)
        if not address:
            continue
        slot = agg.setdefault(
            address, {"symbol": symbols.get(address, ""), "signals": {}, "h24": 0.0}
        )
        for name, value in _pool_signals(pool.get("attributes") or {}).items():
            current = slot["signals"].get(name, 0.0)
            slot["signals"][name] = (
                current + value
                if name in {"volume_24h", "liquidity", "growth"}
                else max(current, value)
            )
        slot["h24"] += (
            _num(((pool.get("attributes") or {}).get("volume_usd") or {}).get("h24")) or 0.0
        )
    rows = [
        {
            "source": "geckoterminal",
            "chain": chain,
            "token_address": address,
            "symbol": slot["symbol"],
            "signals": slot["signals"],
            "paid": False,
        }
        for address, slot in agg.items()
        if slot["h24"] > 0
    ]
    rows.sort(key=lambda row: (-row["signals"]["volume_24h"], row["token_address"]))
    return rows


async def _get_json(url: str) -> Any:
    import httpx

    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": "rugbrain-trending/1.0"}
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def geckoterminal_rows(chain: str, get_json: Fetcher | None = None) -> list[dict[str, Any]]:
    """Discover trending candidates for one chain; raises UnknownNetwork when unwired."""
    network = resolve_network(chain)
    if network is None:
        raise UnknownNetwork(f"geckoterminal: no verified network slug for chain {chain!r}")
    payload = await (get_json or _get_json)(URL.format(network=network))
    return rows_from_payload(payload, chain)
