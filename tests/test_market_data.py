"""Tests for rmi_rag.market_data — CoinPaprika + LunarCrush enrichment."""

from __future__ import annotations

import respx

from rmi_rag import market_data as mod

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
    {
        "symbol": "CCC",
        "name": "Gamma",
        "rank": 3,
        "quotes": {
            "USD": {
                "price": 3.0,
                "volume_24h": 500.0,
                "market_cap": 1000.0,
                "percent_change_24h": 5.0,
            }
        },
    },
]

NEW_PAYLOAD = [
    {"id": "new-token", "name": "New Token", "symbol": "NEW", "is_new": True, "type": "token"},
    {"id": "old-token", "name": "Old Token", "symbol": "OLD", "is_new": False, "type": "token"},
]

LUNARCRUSH_PAYLOAD = {
    "data": [
        {
            "symbol": "AAA",
            "social_score": 99.0,
            "sentiment": 0.5,
            "social_volume_24h": 10.0,
            "galaxy_score": 4.0,
        }
    ]
}


def test_top_gainers_losers_most_traded():
    # Normalized rows shaped like fetch_coinpaprika_tickers output.
    tickers = [
        {
            "symbol": "AAA",
            "name": "Alpha",
            "rank": 1,
            "price_usd": 1.0,
            "volume_24h_usd": 1000.0,
            "market_cap_usd": 5000.0,
            "percent_change_24h": 50.0,
        },
        {
            "symbol": "BBB",
            "name": "Beta",
            "rank": 2,
            "price_usd": 2.0,
            "volume_24h_usd": 9000.0,
            "market_cap_usd": 2000.0,
            "percent_change_24h": -40.0,
        },
        {
            "symbol": "CCC",
            "name": "Gamma",
            "rank": 3,
            "price_usd": 3.0,
            "volume_24h_usd": 500.0,
            "market_cap_usd": 1000.0,
            "percent_change_24h": 5.0,
        },
    ]
    assert mod.top_gainers(tickers)[0]["symbol"] == "AAA"
    assert mod.top_losers(tickers)[0]["symbol"] == "BBB"
    assert mod.most_traded(tickers)[0]["symbol"] == "BBB"
    assert len(mod.top_gainers(tickers, limit=2)) == 2


def test_enrich_with_social_merges_by_symbol():
    tickers = [
        {
            "symbol": "AAA",
            "name": "Alpha",
            "rank": 1,
            "price_usd": 1.0,
            "volume_24h_usd": 0.0,
            "market_cap_usd": 0.0,
            "percent_change_24h": 0.0,
        },
        {
            "symbol": "ZZZ",
            "name": "Zeta",
            "rank": 9,
            "price_usd": 9.0,
            "volume_24h_usd": 0.0,
            "market_cap_usd": 0.0,
            "percent_change_24h": 0.0,
        },
    ]
    social = [
        {
            "symbol": "AAA",
            "social_score": 88.0,
            "sentiment": 0.4,
            "social_volume_24h": 1.0,
            "galaxy_score": 2.0,
        }
    ]
    merged = mod.enrich_with_social(tickers, social)
    assert merged[0]["social_score"] == 88.0
    assert merged[0]["social_sentiment"] == 0.4
    assert "social_score" not in merged[1]


@respx.mock
async def test_fetch_coinpaprika_tickers_maps_rows():
    respx.get("https://api.coinpaprika.com/v1/tickers").respond(200, json=TICKERS_PAYLOAD)
    rows = await mod.fetch_coinpaprika_tickers()
    assert len(rows) == 3
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["percent_change_24h"] == 50.0
    assert rows[0]["volume_24h_usd"] == 1000.0


@respx.mock
async def test_fetch_coinpaprika_tickers_error_returns_empty():
    respx.get("https://api.coinpaprika.com/v1/tickers").respond(500, json={})
    assert await mod.fetch_coinpaprika_tickers() == []


@respx.mock
async def test_fetch_coinpaprika_new():
    respx.get("https://api.coinpaprika.com/v1/coins/new").respond(200, json=NEW_PAYLOAD)
    coins = await mod.fetch_coinpaprika_new()
    assert len(coins) == 2
    assert coins[0]["symbol"] == "NEW"
    assert coins[0]["is_new"] is True


async def test_fetch_lunarcrush_disabled_without_key(monkeypatch):
    monkeypatch.delenv("LUNARCRUSH_API_KEY", raising=False)
    assert await mod.fetch_lunarcrush_social() == []


@respx.mock
async def test_fetch_lunarcrush_social_with_key(monkeypatch):
    monkeypatch.setenv("LUNARCRUSH_API_KEY", "test-key")
    respx.get("https://lunarcrush.com/api/v2").respond(200, json=LUNARCRUSH_PAYLOAD)
    rows = await mod.fetch_lunarcrush_social()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAA"
    assert rows[0]["social_score"] == 99.0


@respx.mock
async def test_market_ticker_snapshot_combined(monkeypatch):
    monkeypatch.setenv("LUNARCRUSH_API_KEY", "test-key")
    respx.get("https://api.coinpaprika.com/v1/tickers").respond(200, json=TICKERS_PAYLOAD)
    respx.get("https://api.coinpaprika.com/v1/coins/new").respond(200, json=NEW_PAYLOAD)
    respx.get("https://lunarcrush.com/api/v2").respond(200, json=LUNARCRUSH_PAYLOAD)
    snapshot = await mod.market_ticker_snapshot()
    assert snapshot["ticker_count"] == 3
    assert snapshot["top_gainers_24h"][0]["symbol"] == "AAA"
    assert snapshot["top_losers_24h"][0]["symbol"] == "BBB"
    assert snapshot["most_traded_24h"][0]["symbol"] == "BBB"
    assert snapshot["newly_listed"][0]["symbol"] == "NEW"
    assert snapshot["social_metrics"][0]["symbol"] == "AAA"
