"""web_search: success, pry fallback, typed refusal when everything fails."""

from __future__ import annotations

import importlib as _il

import httpx
import pytest
import respx

search_mod = _il.import_module("rugbrain.live.search")
web_search = search_mod.web_search

SX = "http://127.0.0.1:8089/search"
PRY = "http://127.0.0.1:9109/mcp"


def _searxng(*rows) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {"title": t, "url": u, "content": f"snippet {t}", "publishedDate": "2026-10-24T10:00:00Z"}
                for t, u in rows
            ]
        },
    )


@pytest.mark.asyncio
@respx.mock
async def test_search_success_keeps_cited_date():
    respx.get(SX).mock(return_value=_searxng(("a", "https://a.example"), ("b", "https://b.example")))
    env = await web_search("bitcoin etf", 3)
    assert env.ok is True
    assert env.source == "searxng"
    assert len(env.data) == 2
    assert env.data[0]["published_at"] == "2026-10-24T10:00:00Z"
    assert env.data[0]["snippet"].startswith("snippet")


@pytest.mark.asyncio
@respx.mock
async def test_search_falls_back_to_pry(monkeypatch):
    monkeypatch.setenv("LIVE_PRY_MCP_URL", PRY)
    respx.get(SX).mock(side_effect=httpx.ReadTimeout("slow"))
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": '{"results": [{"title": "p", "url": "https://p.example", "snippet": "sp", "date": "2026-10-25T01:00:00Z"}]}'}]},
    }
    respx.post(PRY).mock(return_value=httpx.Response(200, text=f"event: message\ndata: {__import__('json').dumps(body)}\n"))
    env = await web_search("btc", 5)
    assert env.ok is True
    assert env.source == "pry-mcp"
    assert env.data[0]["url"] == "https://p.example"


@pytest.mark.asyncio
@respx.mock
async def test_search_refuses_with_named_providers(monkeypatch):
    monkeypatch.delenv("LIVE_PRY_MCP_URL", raising=False)
    respx.get(SX).mock(side_effect=httpx.ConnectTimeout("down"))
    env = await web_search("btc", 5)
    assert env.ok is False
    assert env.data is None
    assert env.error["code"] == "all_providers_failed"
    assert "searxng:timeout" in env.error["reason"]
    assert "pry-mcp:not_configured" in env.error["reason"]
