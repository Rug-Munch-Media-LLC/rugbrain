"""Tests for rugbrain.market_context — RugBrain market context (Part 3).

Covers ``build_market_mood`` and ``build_token_market_context`` with mocked
HTTP providers so no external service is contacted.
"""

from __future__ import annotations

import importlib as _il
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

market_context = _il.import_module("rugbrain.market_context")

mod = market_context


def _transport(
    fear: dict[str, Any],
    chains: list[dict[str, Any]],
    new: list[dict[str, Any]],
    hacks: list[dict[str, Any]],
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


def _fresh_client(*a: Any, **k: Any) -> Any:
    """Build a fresh AsyncClient from the real class (avoids patch recursion)."""
    return _ORIG_ASYNC_CLIENT(*a, **k)


@pytest.mark.asyncio
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
        # Fear seam mocked directly: _fetch_fear_index delegates to
        # rugbrain.fear_index.get_fear_index which opens its own real
        # connections (ClickHouse/Redis/HTTP) — never contact them here.
        patch.object(
            mod,
            "_fetch_fear_index",
            new_callable=AsyncMock,
            return_value={"value": 20.0, "classification": "fearful"},
        ),
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
async def test_build_market_mood_fail_open() -> None:
    """Provider outage yields neutral values, not an exception."""
    transport = httpx.MockTransport(
        handler=lambda request: httpx.Response(503, json={"error": "down"})
    )

    with (
        patch("httpx.AsyncClient", side_effect=lambda *a, **k: _fresh_client(transport=transport)),
        patch.object(
            mod,
            "_fetch_fear_index",
            new_callable=AsyncMock,
            # Model the seam's real degradation contract: provider outage
            # yields the neutral default (fail-open), never a raise.
            return_value={"value": 50.0, "classification": "neutral"},
        ),
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
        # All provider seams mocked: keeps the test hermetic (no real
        # ClickHouse/Redis/HTTP from fear_index) and immune to cross-test
        # event-loop pollution. Freshness stays on the dexscreener transport
        # above — its classification is what this test verifies.
        patch.object(
            mod,
            "_fetch_fear_index",
            new_callable=AsyncMock,
            return_value={"value": 80.0, "classification": "greedy"},
        ),
        patch.object(
            mod,
            "_fetch_chain_activity",
            new_callable=AsyncMock,
            return_value={"tvl": 5e10, "tvl_change_24h": 2.0},
        ),
        patch.object(mod, "_fetch_new_deploys", new_callable=AsyncMock, return_value=1),
        patch.object(mod, "_fetch_recent_exploits", new_callable=AsyncMock, return_value=[]),
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
async def test_build_token_market_context_deployer_freshness_new() -> Any:
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
        # side_effect FACTORY (not return_value): every httpx.AsyncClient(...)
        # call must yield a FRESH client instance. A single shared instance
        # raises "Cannot open a client instance more than once" when both the
        # outer context client and the freshness fetcher open one.
        patch(
            "httpx.AsyncClient",
            side_effect=lambda *a, **k: _fresh_client(
                transport=httpx.MockTransport(handler=handler)
            ),
        ),
        patch.object(
            mod,
            "_fetch_fear_index",
            new_callable=AsyncMock,
            return_value={"value": 50.0, "classification": "neutral"},
        ),
        patch.object(
            mod,
            "_fetch_chain_activity",
            new_callable=AsyncMock,
            return_value={"tvl": 0, "tvl_change_24h": 0.0},
        ),
        patch.object(mod, "_fetch_new_deploys", new_callable=AsyncMock, return_value=0),
        patch.object(mod, "_fetch_recent_exploits", new_callable=AsyncMock, return_value=[]),
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


# ── Direct _fetch_deployer_freshness classification units ────────────────
# Hermetic: exercise the classification logic through its own HTTP seam
# without going through build_token_market_context's gather.


def _dexscreener_transport(pair_created_at: int | None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "dexscreener" not in str(request.url):
            return httpx.Response(404, json={})
        if pair_created_at is None:
            return httpx.Response(200, json={"pairs": []})
        return httpx.Response(
            200,
            json={"pairs": [{"pairCreatedAt": pair_created_at, "baseToken": {"symbol": "T"}}]},
        )

    return httpx.MockTransport(handler=handler)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("age_ms", "expected"),
    [
        (24 * 3600 * 1000, "new"),  # 24h old -> new
        (None, "established"),  # ~Sep 2020 -> established
    ],
    ids=["24h-new", "2020-established"],
)
async def test_fetch_deployer_freshness_classification(age_ms: int | None, expected: str) -> Any:
    # Age is resolved INSIDE the test: a wall-clock value in the parametrize id
    # makes collection non-deterministic, which breaks pytest-xdist (nodes
    # collect different ids) and makes reruns non-reproducible.
    created_ms = 1_600_000_000_000 if age_ms is None else int(time.time() * 1000) - age_ms
    transport = _dexscreener_transport(created_ms)
    with patch(
        "httpx.AsyncClient",
        side_effect=lambda *a, **k: _fresh_client(transport=transport),
    ):
        result = await mod._fetch_deployer_freshness("0x" + "c" * 40, "ethereum")
    assert result == expected


@pytest.mark.asyncio
async def test_fetch_deployer_freshness_unknown_on_provider_error() -> Any:
    """Non-2xx provider response degrades to unknown, never raises."""
    transport = httpx.MockTransport(
        handler=lambda request: httpx.Response(500, json={"error": "boom"})
    )
    with patch(
        "httpx.AsyncClient",
        side_effect=lambda *a, **k: _fresh_client(transport=transport),
    ):
        result = await mod._fetch_deployer_freshness("0x" + "d" * 40, "ethereum")
    assert result == "unknown"


@pytest.mark.asyncio
async def test_fetch_deployer_freshness_unknown_on_empty_pairs() -> Any:
    """No pairs data → unknown (no age signal available)."""
    transport = _dexscreener_transport(None)
    with patch(
        "httpx.AsyncClient",
        side_effect=lambda *a, **k: _fresh_client(transport=transport),
    ):
        result = await mod._fetch_deployer_freshness("0x" + "e" * 40, "ethereum")
    assert result == "unknown"
