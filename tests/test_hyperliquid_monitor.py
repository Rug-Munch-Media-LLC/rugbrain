"""Tests for rugbrain.monitors.hyperliquid_monitor — Hyperliquid perp DEX tracking."""

from __future__ import annotations

import pytest
from typing import Any

from rugbrain.monitors.hyperliquid_monitor import (
    classify_trade,
    classify_liquidation,
    scan_liquidations_from_data,
    WHALE_WATCHLIST,
    BIG_TRADE_THRESHOLD,
    WHALE_TRADE_THRESHOLD,
    MEGA_TRADE_THRESHOLD,
)


class TestTradeClassification:
    """Test trade classification logic."""

    def test_mega_whale_trade(self):
        trade = {"px": "65000", "sz": "20", "side": "B", "coin": "BTC"}
        result = classify_trade(trade)
        assert result["usd_value"] >= MEGA_TRADE_THRESHOLD
        assert "MEGA" in result["tier"]
        assert result["coin"] == "BTC"

    def test_whale_trade(self):
        trade = {"px": "65000", "sz": "2", "side": "B", "coin": "BTC"}
        result = classify_trade(trade)
        assert result["usd_value"] >= WHALE_TRADE_THRESHOLD
        assert result["usd_value"] < MEGA_TRADE_THRESHOLD
        assert "WHALE" in result["tier"]
        assert "MEGA" not in result["tier"]

    def test_minnow_trade(self):
        trade = {"px": "65000", "sz": "0.2", "side": "B", "coin": "BTC"}
        result = classify_trade(trade)
        assert result["usd_value"] >= BIG_TRADE_THRESHOLD
        assert result["usd_value"] < WHALE_TRADE_THRESHOLD
        assert "MINNOW" in result["tier"]

    def test_regular_trade(self):
        trade = {"px": "65000", "sz": "0.001", "side": "B", "coin": "BTC"}
        result = classify_trade(trade)
        assert result["usd_value"] < BIG_TRADE_THRESHOLD
        assert result["tier"] == "regular"

    def test_buy_trade_side(self):
        trade = {"px": "100", "sz": "10", "side": "B", "coin": "ETH"}
        result = classify_trade(trade)
        assert result["is_buy"] is True
        assert "BUY" in result["side"]

    def test_sell_trade_side(self):
        trade = {"px": "100", "sz": "10", "side": "A", "coin": "ETH"}
        result = classify_trade(trade)
        assert result["is_buy"] is False
        assert "SELL" in result["side"]


class TestLiquidationClassification:
    """Test liquidation classification logic."""

    def test_mega_liquidation(self):
        liq = {"coin": "BTC", "side": "B", "sz": "20", "lidPx": "65000"}
        result = classify_liquidation(liq)
        assert result["usd_value"] >= MEGA_TRADE_THRESHOLD
        assert "MEGA" in result["tier"]

    def test_whale_liquidation(self):
        liq = {"coin": "ETH", "side": "A", "sz": "50", "lidPx": "2000"}
        result = classify_liquidation(liq)
        assert result["usd_value"] >= WHALE_TRADE_THRESHOLD
        assert result["usd_value"] < MEGA_TRADE_THRESHOLD
        assert "WHALE" in result["tier"]

    def test_regular_liquidation(self):
        liq = {"coin": "SOL", "side": "B", "sz": "150", "lidPx": "75"}
        result = classify_liquidation(liq)
        assert result["usd_value"] < WHALE_TRADE_THRESHOLD
        assert result["usd_value"] >= BIG_TRADE_THRESHOLD
        assert "WHALE" not in result["tier"]

    def test_long_liquidation(self):
        liq = {"coin": "BTC", "side": "B", "sz": "1", "lidPx": "65000"}
        result = classify_liquidation(liq)
        assert result["is_long"] is True
        assert "LONG" in result["side"]

    def test_short_liquidation(self):
        liq = {"coin": "BTC", "side": "A", "sz": "1", "lidPx": "65000"}
        result = classify_liquidation(liq)
        assert result["is_long"] is False
        assert "SHORT" in result["side"]


class TestLiquidationScan:
    """Test scanning liquidation data."""

    def test_scan_liquidations_from_data(self):
        data = [
            {"coin": "BTC", "side": "B", "sz": "20", "lidPx": "65000", "time": 1000},
            {"coin": "ETH", "side": "A", "sz": "1", "lidPx": "2000", "time": 1001},
        ]
        notable = scan_liquidations_from_data(data)
        assert len(notable) >= 1
        assert any(n["coin"] == "BTC" for n in notable)

    def test_scan_empty_data(self):
        notable = scan_liquidations_from_data([])
        assert notable == []


class TestWhaleWatchlist:
    """Test whale watchlist configuration."""

    def test_watchlist_defined(self):
        assert isinstance(WHALE_WATCHLIST, dict)


class TestEndpoints:
    """Test endpoint configurations."""

    @pytest.mark.asyncio
    async def test_api_call_meta(self):
        from rugbrain.monitors.hyperliquid_monitor import _api_call

        result = await _api_call({"type": "allMids"})
        assert isinstance(result, dict)
        assert len(result) > 100  # 232+ coins

    @pytest.mark.asyncio
    async def test_api_call_mids_has_major(self):
        from rugbrain.monitors.hyperliquid_monitor import _api_call

        result = await _api_call({"type": "allMids"})
        for coin in ["BTC", "ETH", "SOL"]:
            if coin in result:
                assert float(result[coin]) > 0
