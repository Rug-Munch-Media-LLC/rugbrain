"""
Tests for rugbrain.market_awareness — market awareness for token scans.

Covers Fear & Greed classification, suspicious-profile heuristics, exploit
fetching (httpx MockTransport), awareness scoring, and snapshot building.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from rugbrain import market_awareness as mod

# ── Helpers ──────────────────────────────────────────────────────────────


def test_fng_classification():
    assert mod._fng_classification(10) == "fearful"
    assert mod._fng_classification(29) == "fearful"
    assert mod._fng_classification(50) == "neutral"
    assert mod._fng_classification(69) == "neutral"
    assert mod._fng_classification(80) == "greedy"


def test_suspicious_profile():
    assert mod._suspicious_profile({"links": []}) is True
    assert (
        mod._suspicious_profile({"links": [{"label": "Website", "url": "https://x.com"}]}) is False
    )
    assert (
        mod._suspicious_profile({"links": [{"label": "Twitter", "url": "https://x.com"}]}) is False
    )


# ── Fetching (mocked HTTP) ───────────────────────────────────────────────


def _make_transport(json_payload, status=200):
    return httpx.MockTransport(handler=lambda request: httpx.Response(status, json=json_payload))


async def test_fetch_recent_exploits_window():
    now = datetime.now(UTC).timestamp()
    payload = [
        {
            "date": int(now) - 3600,
            "name": "Fresh Hack",
            "amount": 5000000,
            "chain": ["Arbitrum"],
            "technique": "Flash Loan",
        },
        {
            "date": int(now) - 60 * 60 * 24 * 30,
            "name": "Old Hack",
            "amount": 100,
            "chain": ["Ethereum"],
            "technique": "",
        },
    ]
    client = httpx.AsyncClient(transport=_make_transport(payload))
    try:
        exploits = await mod.fetch_recent_exploits(client)
    finally:
        await client.aclose()
    assert len(exploits) == 1
    assert exploits[0]["protocol"] == "Fresh Hack"
    assert exploits[0]["amount_lost_usd"] == 5000000


async def test_fetch_recent_exploits_error():
    client = httpx.AsyncClient(transport=_make_transport({}, status=500))
    try:
        exploits = await mod.fetch_recent_exploits(client)
    finally:
        await client.aclose()
    assert exploits == []


async def test_fetch_trending_scams():
    profiles = [
        {
            "tokenAddress": "0xabc",
            "chainId": "ethereum",
            "url": "https://dexscreener.com/ethereum/0xabc",
            "links": [],
        },
        {
            "tokenAddress": "0xdef",
            "chainId": "solana",
            "url": "https://dexscreener.com/solana/0xdef",
            "links": [{"label": "Website", "url": "https://ok.example"}],
        },
    ]
    coins = [{"id": "tinycoin", "name": "Tiny Coin", "market_cap": 100_000}]

    def handler(request: httpx.Request) -> httpx.Response:
        if "token-profiles" in str(request.url):
            return httpx.Response(200, json=profiles)
        if "coingecko" in str(request.url):
            return httpx.Response(200, json=coins)
        return httpx.Response(404, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler=handler))
    try:
        scams = await mod.fetch_trending_scams(client)
    finally:
        await client.aclose()
    # 0xabc: suspicious (no links) + Tiny Coin: tiny market cap
    assert len(scams) == 2
    tokens = {s["token"] for s in scams}
    assert "0xabc" in tokens
    assert "Tiny Coin" in tokens


async def test_fetch_market_sentiment():
    client = httpx.AsyncClient(transport=_make_transport({"data": [{"value": "15"}]}))
    try:
        sentiment = await mod.fetch_market_sentiment(client)
    finally:
        await client.aclose()
    assert sentiment == "fearful"


# ── Scoring ──────────────────────────────────────────────────────────────


def test_score_awareness_exploit_match():
    snapshot = {
        "recent_exploits": [{"protocol": "Super Sushi Samurai"}],
        "trending_scams": [{"token": "0xabc"}],
        "market_sentiment": "fearful",
    }
    assert mod.score_awareness("Is Super Sushi Samurai safe?", snapshot) == 1.0
    assert mod.score_awareness("check 0xabc token", snapshot) == 1.0


def test_score_awareness_fearful_keyword():
    snapshot = {"recent_exploits": [], "trending_scams": [], "market_sentiment": "fearful"}
    assert mod.score_awareness("is this a rug pull?", snapshot) == 0.75
    assert mod.score_awareness("generic question", snapshot) == 0.5


def test_score_awareness_default():
    snapshot = {"recent_exploits": [], "trending_scams": [], "market_sentiment": "greedy"}
    assert mod.score_awareness("anything", snapshot) == 0.5
    assert mod.score_awareness("", snapshot) == 0.5


def test_boosted_risk_reasons():
    snapshot = {
        "recent_exploits": [{"protocol": "Wormhole"}],
        "trending_scams": [],
        "market_sentiment": "neutral",
    }
    result = mod.boosted_risk("Wormhole bridge hack", snapshot)
    assert result["score"] == 1.0
    assert "recent_exploit:wormhole" in result["reasons"]


# ── Snapshot (mocked client, no cache) ──────────────────────────────────


async def test_market_awareness_snapshot_mocked(monkeypatch):
    async def fake_exploits(client):
        return [{"protocol": "Fresh Hack", "date": "2026-08-01", "amount_lost_usd": 1}]

    async def fake_scams(client):
        return [{"token": "0xabc", "chain": "ethereum", "reasons": ["no_socials"], "url": ""}]

    async def fake_fng(client):
        return "greedy"

    monkeypatch.setattr(mod, "fetch_recent_exploits", fake_exploits)
    monkeypatch.setattr(mod, "fetch_trending_scams", fake_scams)
    monkeypatch.setattr(mod, "fetch_market_sentiment", fake_fng)

    client = httpx.AsyncClient(transport=_make_transport({}))
    try:
        snapshot = await mod.market_awareness_snapshot(client=client, use_cache=False)
    finally:
        await client.aclose()

    assert snapshot["market_sentiment"] == "greedy"
    assert snapshot["recent_exploits"][0]["protocol"] == "Fresh Hack"
    assert snapshot["trending_scams"][0].get("token") == "0xabc"
    assert set(snapshot.keys()) >= {"trending_scams", "recent_exploits", "market_sentiment"}
