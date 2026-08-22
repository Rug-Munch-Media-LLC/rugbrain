"""Tests for rugbrain.market_snapshot — market state digest + ClickHouse log."""

from __future__ import annotations

import importlib as _il
from typing import Any

import pytest
import respx

market_snapshot = _il.import_module("rugbrain.market_snapshot")

mod = market_snapshot

TICKERS_PAYLOAD = [
    {
        "symbol": "AAA",
        "name": "Alpha",
        "rank": 1,
        "quotes": {
            "USD": {
                "price": 1.0,
                "volume_24h": 1000.0,
                "market_cap": 5000.0,
                "percent_change_24h": 50.0,
            }
        },
    },
    {
        "symbol": "BBB",
        "name": "Beta",
        "rank": 2,
        "quotes": {
            "USD": {
                "price": 2.0,
                "volume_24h": 9000.0,
                "market_cap": 2000.0,
                "percent_change_24h": -40.0,
            }
        },
    },
]

NEW_PAYLOAD = [
    {"id": "new-token", "name": "New Token", "symbol": "NEW", "is_new": True, "type": "token"}
]

DEX_PAIRS = {
    "pairs": [
        {
            "pairAddress": "0xpair1",
            "dexId": "uniswap-v2",
            "chainId": "ethereum",
            "baseToken": {"symbol": "AAA"},
            "quoteToken": {"symbol": "USDC"},
            "priceUsd": "1.00",
            "volume": {"h24": 5000000},
            "liquidity": {"usd": 100000},
            "url": "https://dexscreener.com/ethereum/0xpair1",
        }
    ]
}

CHAINS_PAYLOAD = [{"name": "Ethereum", "tvl": 50000000000.0, "change_1d": 2.5, "protocols": 300}]

FNG_PAYLOAD = {"data": [{"value": "25", "value_classification": "Fear"}]}


@respx.mock
async def test_build_market_snapshot() -> None:
    respx.get("https://api.coinpaprika.com/v1/tickers").respond(200, json=TICKERS_PAYLOAD)
    respx.get("https://api.coinpaprika.com/v1/coins/new").respond(200, json=NEW_PAYLOAD)
    respx.get("https://api.dexscreener.com/latest/dex/search").respond(200, json=DEX_PAIRS)
    respx.get("https://api.llama.fi/v2/chains").respond(200, json=CHAINS_PAYLOAD)
    respx.get("https://api.alternative.me/fng/").respond(200, json=FNG_PAYLOAD)

    snapshot = await mod.build_market_snapshot()
    assert snapshot["top_gainers_24h"][0]["symbol"] == "AAA"
    assert snapshot["top_losers_24h"][0]["symbol"] == "BBB"
    assert snapshot["most_traded_24h"][0]["symbol"] == "BBB"
    assert snapshot["newly_listed"][0]["symbol"] == "NEW"
    assert snapshot["top_dex_pairs"][0]["volume_24h_usd"] == 5000000
    assert snapshot["tvl_changes_by_chain"][0]["chain"] == "Ethereum"
    assert snapshot["fear_greed"]["value"] == 25
    assert snapshot["fear_greed"]["classification"] == "fearful"


@respx.mock
async def test_fetch_fear_greed_neutral_on_failure() -> None:
    respx.get("https://api.alternative.me/fng/").respond(500, json={})
    async with _client() as client:
        result = await mod.fetch_fear_greed(client)
    assert result["value"] == 50
    assert result["classification"] == "neutral"


@respx.mock
async def test_fetch_tvl_changes_parses() -> None:
    respx.get("https://api.llama.fi/v2/chains").respond(200, json=CHAINS_PAYLOAD)
    async with _client() as client:
        rows = await mod.fetch_tvl_changes(client)
    assert rows[0]["change_1d"] == 2.5


def test_market_snapshot_store_degrades_without_clickhouse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICKHOUSE_HOST", "127.0.0.1")
    monkeypatch.setenv("CLICKHOUSE_PORT", "1")
    store = mod.MarketSnapshotStore()
    assert (
        store.store(
            {
                "generated_at": "2026-08-04T00:00:00Z",
                "fear_greed": {"value": 50, "classification": "neutral"},
            }
        )
        is False
    )
    assert store.recent(limit=3) == []


def _client() -> Any:
    import httpx

    return httpx.AsyncClient(timeout=10.0)
