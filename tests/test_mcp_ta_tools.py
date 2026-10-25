"""MCP tool-surface tests for tools_ta: registration, success, refusal, validation.

No live HTTP — ``_call`` / ``_call_search_multi`` are stubbed, so the tests prove
the tool contract (envelope, typed refusal, arg validation), not the backend.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugbrain.rugbrain_mcp import tools_ta
from rugbrain.rugbrain_mcp.app import mcp

NEW_TOOLS = {"ta_signals", "ta_indicators", "market_odds"}


async def _invoke(name: str, **kwargs: Any) -> Any:
    """Call a tool whether the decorator returns the function or a Tool object."""
    fn = getattr(tools_ta, name)
    if callable(fn) and not hasattr(fn, "run"):
        return await fn(**kwargs)
    tool = await mcp.get_tool(name)
    return await tool.run(kwargs)


def _run(name: str, **kwargs: Any) -> Any:
    return asyncio.run(_invoke(name, **kwargs))


def test_new_tools_are_registered() -> None:
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names >= NEW_TOOLS


def test_ta_signals_success_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_ta,
        "_call",
        lambda path, params=None: {
            "pair": "solana:0xabc",
            "candles": 168,
            "trend": "up",
            "signal_count": 1,
            "signals": [{"kind": "liquidity_collapse", "confidence": 0.91}],
        },
    )
    res = _run("ta_signals", pair="0xabc", hours=168, chain="solana")
    sc = res.structured_content
    assert sc["status"] == "ok"
    assert sc["tool"] == "ta_signals"
    assert sc["signal_count"] == 1 and sc["trend"] == "up"


def test_ta_indicators_refuses_when_ohlcv_down(monkeypatch) -> None:
    def boom(path: str, params: Any = None) -> Any:
        raise RuntimeError("RugBrain HTTP 503: OHLCV store unavailable")

    monkeypatch.setattr(tools_ta, "_call", boom)
    sc = _run("ta_indicators", pair="0xabc", hours=24, chain="solana").structured_content
    assert sc["status"] == "refused"
    assert sc["error"]["code"] == "ohlcv_unavailable"


def test_market_odds_refuses_when_lane_empty(monkeypatch) -> None:
    async def empty(**kwargs: Any) -> dict[str, Any]:
        return {"results": []}

    monkeypatch.setattr(tools_ta, "_call_search_multi", empty)
    sc = _run("market_odds", query="BTC above 100k by December").structured_content
    assert sc["status"] == "refused"
    assert sc["error"]["code"] == "no_matches"


def test_empty_pair_and_bad_window_are_invalid_argument() -> None:
    blank = _run("ta_signals", pair="   ", hours=24, chain="solana").structured_content
    assert blank["error"]["code"] == "invalid_argument"
    wide = _run("ta_indicators", pair="0xabc", hours=1, chain="solana").structured_content
    assert wide["error"]["code"] == "invalid_argument"


def test_indicators_route_returns_numbers(monkeypatch) -> None:
    """The new /api/v1/ta/indicators route computes a real snapshot, not an empty dict."""
    from datetime import UTC, datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rugbrain.routes import ta as ta_route
    from rugbrain.ta.candles import Candle, Series

    start = datetime(2026, 9, 1, tzinfo=UTC)
    candles = [
        Candle(
            timestamp=start + timedelta(hours=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0 + i,
            trades=3 + i,
            chain="solana",
            pair_address="0xabc",
        )
        for i in range(60)
    ]
    monkeypatch.setattr(ta_route, "fetch_candles", lambda ref, hours=168: Series(candles))
    app = FastAPI()
    app.include_router(ta_route.router)
    body = TestClient(app).get(
        "/api/v1/ta/indicators", params={"chain": "solana", "pair": "0xabc", "hours": 48}
    ).json()
    assert body["candles"] == 60
    assert isinstance(body["rsi14"], (int, float))
    assert body["sma20"] is not None and body["close"] == 159.5
