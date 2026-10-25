"""live_price: success, stale rejection, timeout refusal, provider fallback."""

from __future__ import annotations

import importlib as _il
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

price_mod = _il.import_module("rugbrain.live.price")
live_price = price_mod.live_price

RM = "http://127.0.0.1:9105/mcp"
CG = "https://api.coingecko.com/api/v3"
MINT = "So11111111111111111111111111111111111111112"


def _epoch(delta_s: float = 0) -> int:
    return int((datetime.now(UTC) - timedelta(seconds=delta_s)).timestamp())


def _cg_price(ts: int, usd: float = 181.5) -> httpx.Response:
    return httpx.Response(200, json={MINT: {"usd": usd, "last_updated_at": ts}})


def _mcp_price(usd: float) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": f'{{"price": {usd}}}'}]}}
    return httpx.Response(200, text=f"event: message\ndata: {__import__('json').dumps(body)}\n")


@pytest.mark.asyncio
@respx.mock
async def test_price_success_from_coingecko():
    respx.post(RM).mock(return_value=httpx.Response(503))
    respx.get(f"{CG}/simple/token_price/solana").mock(return_value=_cg_price(_epoch()))
    env = await live_price(MINT, "solana")
    assert env.ok is True
    assert env.source == "coingecko"
    assert env.data["price_usd"] == 181.5
    assert env.fetched_at.endswith("Z")


@pytest.mark.asyncio
@respx.mock
async def test_price_prefers_rugmunch_and_skips_coingecko():
    respx.post(RM).mock(return_value=_mcp_price(179.25))
    cg = respx.get(f"{CG}/simple/token_price/solana").mock(return_value=_cg_price(_epoch()))
    env = await live_price(MINT, "solana")
    assert env.ok is True
    assert env.source == "rugmunch-data"
    assert env.data["price_usd"] == 179.25
    assert cg.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_price_stale_is_rejected():
    respx.post(RM).mock(return_value=httpx.Response(503))
    respx.get(f"{CG}/simple/token_price/solana").mock(return_value=_cg_price(_epoch(600)))
    env = await live_price(MINT, "solana")
    assert env.ok is False
    assert env.stale is True
    assert env.data is None
    assert env.error["code"] == "stale"
    assert "coingecko" in env.error["reason"]


@pytest.mark.asyncio
@respx.mock
async def test_price_timeout_refuses_naming_provider():
    respx.post(RM).mock(side_effect=httpx.ConnectTimeout("down"))
    respx.get(f"{CG}/simple/token_price/solana").mock(side_effect=httpx.ReadTimeout("slow"))
    env = await live_price(MINT, "solana")
    assert env.ok is False
    assert env.error["code"] == "all_providers_failed"
    assert "rugmunch-data:timeout" in env.error["reason"]
    assert "coingecko:timeout" in env.error["reason"]
