"""Tests for rugbrain.liquidation_heatmap — comprehensive liquidation tracking."""

from __future__ import annotations

import pytest

from rugbrain.liquidation_heatmap import (
    CEX_LIQUIDATION_ENDPOINTS,
    LIQUIDATION_CONTRACTS,
    get_binance_liquidations,
    get_bybit_liquidations,
    get_hype_liquidations,
    get_liquidation_wins,
    get_okx_liquidations,
)


class TestLiquidationEndpoints:
    """Verify all liquidation endpoints are configured."""

    def test_liquidation_contracts_defined(self) -> None:
        assert len(LIQUIDATION_CONTRACTS) >= 4
        assert "Aave V2" in LIQUIDATION_CONTRACTS.values()
        assert "Compound" in LIQUIDATION_CONTRACTS.values()

    def test_cex_endpoints_defined(self) -> None:
        assert "Binance" in CEX_LIQUIDATION_ENDPOINTS
        assert "Bybit" in CEX_LIQUIDATION_ENDPOINTS
        assert "fapi.binance.com" in CEX_LIQUIDATION_ENDPOINTS["Binance"]
        assert "api.bybit.com" in CEX_LIQUIDATION_ENDPOINTS["Bybit"]


class TestLiquidationClassification:
    """Test that liquidation events are classified correctly."""

    def test_hype_liquidation_mega(self) -> None:
        events = [{"value_usd": 15_000_000, "exchange": "Binance", "symbol": "BTCUSDT"}]
        hype = get_hype_liquidations(events)
        assert len(hype) == 1
        assert "MEGA" in hype[0]["hype_level"]

    def test_hype_liquidation_large(self) -> None:
        events = [{"value_usd": 750_000, "exchange": "Binance", "symbol": "ETHUSDT"}]
        hype = get_hype_liquidations(events)
        assert len(hype) == 1
        assert "LARGE" in hype[0]["hype_level"]

    def test_hype_liquidation_notable(self) -> None:
        events = [{"value_usd": 150_000, "exchange": "Bybit", "symbol": "SOLUSDT"}]
        hype = get_hype_liquidations(events)
        assert len(hype) == 1
        assert "NOTABLE" in hype[0]["hype_level"]

    def test_no_hype_for_small(self) -> None:
        events = [{"value_usd": 1000, "exchange": "OKX", "symbol": "BTCUSDT"}]
        hype = get_hype_liquidations(events)
        assert len(hype) == 0

    def test_liquidation_win_profitable(self) -> None:
        events = [{"value_usd": 2_000_000, "exchange": "Binance"}]
        wins = get_liquidation_wins(events)
        assert len(wins) == 1
        assert "PROFITABLE" in wins[0]["win_level"]
        assert wins[0]["estimated_liquidator_profit"] > 50000

    def test_liquidation_win_good(self) -> None:
        events = [{"value_usd": 300_000, "exchange": "Bybit"}]
        wins = get_liquidation_wins(events)
        assert len(wins) == 1
        assert "GOOD" in wins[0]["win_level"]

    def test_liquidation_win_decent(self) -> None:
        events = [{"value_usd": 100_000, "exchange": "OKX"}]
        wins = get_liquidation_wins(events)
        assert len(wins) == 1
        assert "DECENT" in wins[0]["win_level"]

    def test_no_win_for_small(self) -> None:
        events = [{"value_usd": 1000, "exchange": "Binance"}]
        wins = get_liquidation_wins(events)
        assert len(wins) == 0

    def test_empty_events_no_hype(self) -> None:
        hype = get_hype_liquidations([])
        assert hype == []

    def test_empty_events_no_wins(self) -> None:
        wins = get_liquidation_wins([])
        assert wins == []


class TestLiquidationCycle:
    """Test the full liquidation scan cycle produces expected shape."""

    @pytest.mark.asyncio
    async def test_scan_cycle_shape(self) -> None:
        from rugbrain.liquidation_heatmap import liquidation_scan_cycle

        result = await liquidation_scan_cycle()
        assert "scanned_at" in result
        assert "total_events" in result
        assert "volume_24h_usd" in result
        assert "by_exchange" in result
        assert "hype_liquidations" in result
        assert "liquidation_wins" in result
        assert "biggest_single" in result
        assert "alerts" in result
        assert isinstance(result["total_events"], int)
        assert isinstance(result["volume_24h_usd"], float)
        assert isinstance(result["by_exchange"], dict)


class TestExchangeLiquidations:
    """Test per-exchange liquidation fetching."""

    @pytest.mark.asyncio
    async def test_binance_fetch_returns_list(self) -> None:
        events = await get_binance_liquidations()
        assert isinstance(events, list)
        for e in events:
            assert "exchange" in e
            assert e["exchange"] == "Binance"
            assert "value_usd" in e
            assert "severity" in e

    @pytest.mark.asyncio
    async def test_bybit_fetch_returns_list(self) -> None:
        events = await get_bybit_liquidations()
        assert isinstance(events, list)
        for e in events:
            assert "exchange" in e
            assert e["exchange"] == "Bybit"
            assert "value_usd" in e

    @pytest.mark.asyncio
    async def test_okx_fetch_returns_list(self) -> None:
        events = await get_okx_liquidations()
        assert isinstance(events, list)
        for e in events:
            assert "exchange" in e
            assert e["exchange"] == "OKX"
            assert "value_usd" in e
