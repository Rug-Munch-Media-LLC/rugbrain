"""``/api/v1/ta/indicators``: indicator math on a fixture + the refusal paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rugbrain.routes import ta_indicators as mod
from rugbrain.ta.candles import Candle, Series
from rugbrain.ta.reader import CandleReadError

START = datetime(2026, 10, 1, tzinfo=UTC)


def _series(n: int = 60, *, falling: bool = False, volume_spike: bool = False) -> Series:
    out = []
    for i in range(n):
        base = 100.0 - i if falling else 100.0 + i
        vol = 1000.0 if not (volume_spike and i == n - 1) else 5000.0
        out.append(
            Candle(
                timestamp=START + timedelta(hours=i),
                open=base,
                high=base + 1.0,
                low=base - 1.0,
                close=base + 0.5,
                volume=vol,
                trades=5,
                chain="solana",
                pair_address="So1Pair",
            )
        )
    return Series(out)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app)


def _serve(monkeypatch: pytest.MonkeyPatch, series: Series) -> None:
    monkeypatch.setattr(mod, "fetch_candles", lambda ref, hours=168, **_: series)


def test_rsi_atr_trend_on_fixture(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _series())
    body = client.get("/api/v1/ta/indicators?chain=solana&pair=So1Pair").json()
    assert body["candles"] == 60
    assert body["rsi14"] == pytest.approx(100.0, abs=0.01)  # monotonic rise
    assert body["trend"] == "bull"
    assert body["atr14"] == pytest.approx(2.0, abs=0.01)  # TR = 2.0 every bar
    assert body["atr_pct"] == pytest.approx(2.0 / 159.5 * 100.0, abs=0.01)
    assert body["drawdown_from_peak"] == pytest.approx(0.0)
    assert body["as_of"] == (START + timedelta(hours=59)).isoformat()
    assert body["first_candle"] == START.isoformat()


def test_drawdown_and_bear_trend(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _series(falling=True))
    body = client.get("/api/v1/ta/indicators?chain=solana&pair=So1Pair").json()
    assert body["trend"] == "bear"
    assert body["rsi14"] == pytest.approx(0.0, abs=0.01)
    assert body["drawdown_from_peak"] < -0.5
    assert body["peak_ts"] == START.isoformat()


def test_abnormal_volume_flag(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _series(volume_spike=True))
    body = client.get("/api/v1/ta/indicators?chain=solana&pair=So1Pair").json()
    assert body["volume_abnormal"] is True
    assert body["volume_ratio"] > 2.0


def test_refuses_when_ohlcv_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, Series([]))
    resp = client.get("/api/v1/ta/indicators?chain=solana&pair=Nope")
    assert resp.status_code == 404
    assert "no OHLCV candles" in resp.json()["detail"]


def test_refuses_when_store_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(ref, hours=168, **_) -> Series:
        raise CandleReadError("clickhouse_connect is not installed on this host")

    monkeypatch.setattr(mod, "fetch_candles", boom)
    resp = client.get("/api/v1/ta/indicators?chain=solana&pair=So1Pair")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "OHLCV store unavailable"


def test_short_series_yields_nulls_not_numbers(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _series(n=10))
    body = client.get("/api/v1/ta/indicators?chain=solana&pair=So1Pair").json()
    assert body["rsi14"] is None
    assert body["atr14"] is None
    assert body["trend"] == "flat"
