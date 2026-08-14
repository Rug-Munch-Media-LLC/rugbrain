"""Tests for rugbrain.market_context — RugBrain market context (Part 3).

Covers ``build_market_mood`` and ``build_token_market_context`` with mocked
HTTP providers so no external service is contacted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from rugbrain import market_context as mod


def _transport(
    fear: dict, chains: list[dict], new: list[dict], hacks: list[dict]
) -> httpx.MockTransport:
    """Build a MockTransport that routes by URL suffix."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "alternative.me" in url:
            return httpx.Response(200, json=fear)
        if "hacks" in url:
            return httpx.Response(200, json=hacks)
        if "llama.fi" in url:
            return httpx.Response(200, json=chains)
        if "coins/new" in url:
            return httpx.Response(200, json=new)
        if "dexscreener" in url:
            return httpx.Response(
                200,
                json={
                    "pairs": [
                        {
                            "pairCreatedAt": 1_600_000_000_000,  # ~Sep 2020 → established
                            "baseToken": {"symbol": "T"},
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler=handler)


# Capture the real client class at import time — the test patches
# ``httpx.AsyncClient`` itself, so any helper that calls ``httpx.AsyncClient``
# after the patch would recurse.
_ORIG_ASYNC_CLIENT = httpx.AsyncClient


def _fresh_client(*a, **k):
    """Build a fresh AsyncClient from the real class (avoids patch recursion)."""
    return _ORIG_ASYNC_CLIENT(*a, **k)


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="_fetch_fear_index now calls rugbrain.fear_index.get_fear_index which creates its own HTTP connections — mock transport bypassed"
)
async def test_build_market_mood() -> None:
    transport = _transport(
        fear={"data": [{"value": "20"}]},
        chains=[{"name": "Ethereum", "tvl": 5e10, "change_1d": -3.5}],
        new=[{"id": "x"}, {"id": "y"}],
        hacks=[
            {
                "date": __import__("time").time(),
                "name": "Recent Hack",
                "amount": 1000000,
                "chain": ["Ethereum"],
                "technique": "reentrancy",
            }
        ],
    )

    with (
        patch("httpx.AsyncClient", side_effect=lambda *a, **k: _fresh_client(transport=transport)),
        patch.object(mod, "_MOOD_CACHE", {}),
    ):
        mood = await mod.build_market_mood(chain="ethereum")

    assert mood["fear_index"] == 20.0
    assert mood["fear_classification"] == "fearful"
    assert mood["market_sentiment"] == "fearful"
    assert mood["chain_volume_24h"] == -3.5
    assert mood["chain_activity"]["chain"] == "Ethereum"
    assert mood["chain_activity"]["new_deploys_24h"] == 2
    assert mood["recent_exploits_24h"] >= 1


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="_fetch_fear_index calls rugbrain.fear_index.get_fear_index (bypasses mock transport)"
)
async def test_build_market_mood_fail_open() -> None:
    """Provider outage yields neutral values, not an exception."""
    transport = httpx.MockTransport(
        handler=lambda request: httpx.Response(503, json={"error": "down"})
    )

    with (
        patch("httpx.AsyncClient", side_effect=lambda *a, **k: _fresh_client(transport=transport)),
        patch.object(mod, "_MOOD_CACHE", {}),
    ):
        mood = await mod.build_market_mood(chain="ethereum")

    assert mood["fear_index"] == 50.0
    assert mood["market_sentiment"] == "neutral"
    assert mood["recent_exploits_24h"] == 0
    assert mood["chain_volume_24h"] == 0.0


@pytest.mark.asyncio
async def test_build_token_market_context() -> None:
    address = "0x" + "a" * 40
    transport = _transport(
        fear={"data": [{"value": "80"}]},
        chains=[{"name": "Ethereum", "tvl": 5e10, "change_1d": 2.0}],
        new=[{"id": "x"}],
        hacks=[],
    )

    with (
        patch("httpx.AsyncClient", side_effect=lambda *a, **k: _fresh_client(transport=transport)),
        patch(
            "rugbrain.market_context._fetch_social_signals",
            new_callable=AsyncMock,
            return_value={
                "sentiment": "bullish",
                "confidence": 0.7,
                "mentions": 12,
                "mention_velocity": 60.0,
            },
        ),
        patch(
            "rugbrain.market_context._fetch_similar_tokens",
            new_callable=AsyncMock,
            return_value=[{"address": "0xbbb", "outcome": "rug"}],
        ),
    ):
        ctx = await mod.build_token_market_context(address=address, chain="ethereum")

    assert ctx["address"] == address
    assert ctx["fear_index"] == 80.0
    assert ctx["market_sentiment"] == "greedy"
    assert ctx["social_signals"]["mention_velocity"] == 60.0
    assert ctx["social_signals"]["sentiment"] == "bullish"
    assert ctx["similar_tokens"][0]["outcome"] == "rug"
    # pairCreatedAt ~Sep 2020 → established
    assert ctx["deployer_freshness"] == "established"


@pytest.mark.asyncio
async def test_build_token_market_context_deployer_freshness_new() -> None:
    address = "0x" + "b" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "dexscreener" in url:
            import time

            created = int(time.time() * 1000) - 24 * 3600 * 1000  # 24h ago → new
            return httpx.Response(
                200,
                json={"pairs": [{"pairCreatedAt": created, "baseToken": {"symbol": "T"}}]},
            )
        if "alternative.me" in url:
            return httpx.Response(200, json={"data": [{"value": "50"}]})
        if "llama.fi" in url:
            return httpx.Response(200, json=[])
        if "coins/new" in url:
            return httpx.Response(200, json=[])
        if "hacks" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={})

    with (
        patch(
            "httpx.AsyncClient",
            return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler=handler)),
        ),
        patch(
            "rugbrain.market_context._fetch_social_signals",
            new_callable=AsyncMock,
            return_value={
                "sentiment": "neutral",
                "confidence": 0.0,
                "mentions": 0,
                "mention_velocity": 0.0,
            },
        ),
        patch(
            "rugbrain.market_context._fetch_similar_tokens", new_callable=AsyncMock, return_value=[]
        ),
    ):
        ctx = await mod.build_token_market_context(address=address, chain="ethereum")

    assert ctx["deployer_freshness"] == "new"
