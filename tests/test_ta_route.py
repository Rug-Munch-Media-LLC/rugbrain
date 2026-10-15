"""TA tool-surface tests: the /ta/signals route over a stubbed OHLCV reader.

No live ClickHouse — ``fetch_candles`` is monkeypatched to return a synthetic
series, so the test proves the route's contract (shape, trend, signal list,
error mapping) rather than the store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rugbrain.routes import ta as ta_route
from rugbrain.ta.candles import Candle, Series
from rugbrain.ta.reader import CandleReadError

START = datetime(2026, 9, 1, tzinfo=UTC)


def _series(n: int = 120, *, rising: bool = True) -> Series:
    candles = []
    for i in range(n):
        base = 100.0 + (i if rising else -i)
        candles.append(
            Candle(
                timestamp=START + timedelta(hours=i),
                open=base,
                high=base + 1.0,
                low=base - 1.0,
                close=base + 0.5,
                volume=1000.0 + i,
                trades=3 + i,
                chain="ethereum",
                pair_address="0xabc",
            )
        )
    return Series(candles)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ta_route.router)
    return TestClient(app)


def test_signals_returns_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ta_route, "fetch_candles", lambda ref, hours=168: _series())
    resp = client.get("/api/v1/ta/signals", params={"chain": "ethereum", "pair": "0xabc"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pair"] == "ethereum:0xabc"
    assert body["candles"] == 120
    assert body["trend"] in ("bull", "bear", "flat")
    assert isinstance(body["signals"], list)
    assert body["signal_count"] == len(body["signals"])


def test_signals_empty_series_is_not_an_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ta_route, "fetch_candles", lambda ref, hours=168: Series([]))
    resp = client.get("/api/v1/ta/signals", params={"chain": "ethereum", "pair": "0xabc"})
    assert resp.status_code == 200
    assert resp.json()["signals"] == []


def test_signals_store_down_maps_to_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(ref, hours=168):
        raise CandleReadError("clickhouse down")

    monkeypatch.setattr(ta_route, "fetch_candles", _boom)
    resp = client.get("/api/v1/ta/signals", params={"chain": "ethereum", "pair": "0xabc"})
    assert resp.status_code == 503


def test_signals_bad_window_maps_to_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _bad(ref, hours=168):
        raise ValueError("hours exceeds MAX_WINDOW_HOURS")

    monkeypatch.setattr(ta_route, "fetch_candles", _bad)
    resp = client.get("/api/v1/ta/signals", params={"chain": "ethereum", "pair": "0xabc"})
    assert resp.status_code == 400


def test_stats_route(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ta_route,
        "table_stats",
        lambda: {"rows": 2640471, "pairs": 53935, "fresh": "2026-10-15 00:00:00"},
    )
    resp = client.get("/api/v1/ta/stats")
    assert resp.status_code == 200
    assert resp.json()["rows"] == 2640471
