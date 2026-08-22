"""Tests for rugbrain.market_health — stablecoin pegs, funding rates, gas."""

from __future__ import annotations

import pytest

from rugbrain.market_health import (
    STABLECOINS,
    get_funding_rates,
    get_gas_price,
    get_stablecoin_peg,
    market_health_cycle,
)


class TestMarketHealthConfig:
    """Verify market health configuration."""

    def test_stablecoins_defined(self) -> None:
        assert len(STABLECOINS) >= 3
        assert "DAI" in STABLECOINS
        assert "USDC" in STABLECOINS
        assert "USDT" in STABLECOINS

    def test_stablecoin_addresses_valid(self) -> None:
        for _name, addr in STABLECOINS.items():
            assert addr.startswith("0x")
            assert len(addr) == 42

    @pytest.mark.asyncio
    async def test_get_stablecoin_peg_returns_dict(self) -> None:
        pegs = await get_stablecoin_peg()
        assert isinstance(pegs, dict)
        assert "DAI" in pegs
        assert "USDC" in pegs
        assert "USDT" in pegs
        for _coin, price in pegs.items():
            assert isinstance(price, float)
            assert price > 0.5  # Not zero

    @pytest.mark.asyncio
    async def test_get_funding_rates_returns_list(self) -> None:
        rates = await get_funding_rates()
        assert isinstance(rates, list)
        for r in rates:
            assert "symbol" in r
            assert "funding_rate" in r
            assert "funding_time" in r

    @pytest.mark.asyncio
    async def test_get_gas_price_returns_dict(self) -> None:
        gas = await get_gas_price()
        assert isinstance(gas, dict)
        assert "safe_gwei" in gas
        assert "propose_gwei" in gas
        assert "fast_gwei" in gas

    @pytest.mark.asyncio
    async def test_market_health_cycle_shape(self) -> None:
        result = await market_health_cycle()
        assert "scanned_at" in result
        assert "stablecoin_pegs" in result
        assert "peg_deviations_pct" in result
        assert "funding_rates" in result
        assert "gas_prices" in result
        assert "alerts" in result
        assert isinstance(result["alerts"], list)
