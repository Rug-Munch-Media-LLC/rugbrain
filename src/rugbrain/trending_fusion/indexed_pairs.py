"""Our own indexed pairs as the second discovery source (rmi-backend ClickHouse).

VERIFIED LIVE 2026-10-25 (rmi-backend 127.0.0.1:8000, row from
``source: own_clickhouse:deployer_events``):
  GET http://127.0.0.1:8000/api/v1/market/new-pairs?chain={chain}&limit={n}
  envelope -> {"data": [...], "source": "...", "count": n}
  row      -> chain, block_timestamp, token_address, deployer, factory,
              tx_hash, type, source
  (The sibling /api/v1/market/trending feed was rejected as a source: its rows
  are Coingecko aggregates with chain="multi" and token_address="" - no address,
  no chain, not dependable candidates. Those rows are skipped by contract.)

FIELD MAP into the shared candidate row (fuse.py contract):
  token_address <- token_address
  symbol        <- symbol when the indexer supplies it, else ""
  age           <- 1 / (1 + hours_since(block_timestamp) / 24)
  volume_24h/liquidity/growth/momentum_24h/buy_pressure <- supplied only when the
  indexer emits them (aliases below); otherwise 0 and honestly absent from ranks.
No paid marker: first-seen pairs are organic.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

URL = "http://127.0.0.1:8000/api/v1/market/new-pairs"
ENV = "RUGBRAIN_INDEXED_PAIRS_URL"
LIMIT = 50
Fetcher = Callable[[str], Awaitable[Any]]

ALIASES: dict[str, tuple[str, ...]] = {
    "token_address": ("token_address", "token", "address", "base_token"),
    "symbol": ("symbol", "ticker"),
    "volume_24h": ("volume_usd", "volume_24h", "volume", "volume24h"),
    "liquidity": ("liquidity", "reserve_usd", "reserve_in_usd"),
    "growth": ("trades", "txns", "transactions"),
    "momentum_24h": ("price_change_pct", "price_change", "change_24h"),
    "buy_pressure": ("buy_pressure", "buy_pressure_1h"),
    "created": ("block_timestamp", "created_at", "pair_created_at"),
}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("data", "items", "rows", "result", "pairs"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
            if isinstance(value, Mapping) and isinstance(value.get("items"), list):
                return [item for item in value["items"] if isinstance(item, Mapping)]
    return []


def _pick(row: Mapping[str, Any], field: str) -> Any:
    for key in ALIASES[field]:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def _age(raw: Any) -> float:
    text = str(raw or "").replace("Z", "+00:00")
    try:
        created = datetime.fromisoformat(text)
    except ValueError:
        return 0.5
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    hours = max((datetime.now(timezone.utc) - created).total_seconds() / 3600.0, 0.0)
    return 1.0 / (1.0 + hours / 24.0)


def _signals(row: Mapping[str, Any]) -> dict[str, float]:
    buys, sells = _num(row.get("buys")), _num(row.get("sells"))
    pressure = _num(_pick(row, "buy_pressure"))
    if pressure is None and buys is not None and sells is not None and (buys + sells) > 0:
        pressure = buys / (buys + sells)
    return {
        "volume_24h": _num(_pick(row, "volume_24h")) or 0.0,
        "liquidity": _num(_pick(row, "liquidity")) or 0.0,
        "growth": _num(_pick(row, "growth")) or 0.0,
        "momentum_24h": abs(_num(_pick(row, "momentum_24h")) or 0.0),
        "buy_pressure": pressure or 0.0,
        "age": _age(_pick(row, "created")),
    }


def rows_from_payload(payload: Any, chain: str) -> list[dict[str, Any]]:
    """Fold indexed new-pair rows into candidate rows for one chain."""
    wanted = (chain or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for item in _items(payload):
        item_chain = str(item.get("chain") or "").strip().lower()
        if item_chain and item_chain != wanted:
            continue
        address = str(_pick(item, "token_address") or "").strip()
        if not address:
            continue
        rows.append({"source": "rmi_indexed_pairs", "chain": chain, "token_address": address.lower(),
                     "symbol": _pick(item, "symbol") or "", "signals": _signals(item), "paid": False})
    rows.sort(key=lambda row: (-row["signals"]["age"], row["token_address"]))
    return rows


async def _get_json(url: str) -> Any:
    import httpx

    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "rugbrain-trending/1.0"}) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def indexed_pair_rows(chain: str, get_json: Fetcher | None = None) -> list[dict[str, Any]]:
    """Discover candidates from our own indexed new pairs for one chain."""
    base = os.environ.get(ENV) or URL
    url = f"{base}?chain={chain}&limit={LIMIT}"
    return rows_from_payload(await (get_json or _get_json)(url), chain)
