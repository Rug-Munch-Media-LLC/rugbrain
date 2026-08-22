"""Tests for rugbrain.exchange_flow — exchange balance monitoring."""

from __future__ import annotations

import pytest

from rugbrain.exchange_flow import (
    EXCHANGE_WALLETS,
    LARGE_TX_THRESHOLD_ETH,
    exchange_flow_cycle,
    get_exchange_balance,
    get_exchange_txns,
)


class TestExchangeFlowConfig:
    """Verify exchange flow configuration."""

    def test_exchange_wallets_defined(self) -> None:
        assert len(EXCHANGE_WALLETS) >= 5
        assert "Binance" in EXCHANGE_WALLETS
        assert "Coinbase" in EXCHANGE_WALLETS
        assert "Kraken" in EXCHANGE_WALLETS
        assert "Bybit" in EXCHANGE_WALLETS
        assert "OKX" in EXCHANGE_WALLETS

    def test_binance_has_multiple_wallets(self) -> None:
        assert len(EXCHANGE_WALLETS["Binance"]) >= 3

    def test_wallet_addresses_valid(self) -> None:
        for exchange, wallets in EXCHANGE_WALLETS.items():
            for w in wallets:
                assert w.startswith("0x")
                assert len(w) == 42, f"{exchange} wallet {w} invalid"

    def test_large_tx_threshold(self) -> None:
        assert LARGE_TX_THRESHOLD_ETH > 0
        assert LARGE_TX_THRESHOLD_ETH >= 100

    @pytest.mark.asyncio
    async def test_get_exchange_balance_returns_dict(self) -> None:
        for exchange, wallets in EXCHANGE_WALLETS.items():
            bal = await get_exchange_balance(exchange, wallets[0])
            assert "balance_eth" in bal
            assert "wallet" in bal
            assert isinstance(bal["balance_eth"], float)
            break  # Just test first exchange

    @pytest.mark.asyncio
    async def test_get_exchange_txns_returns_list(self) -> None:
        for wallets in EXCHANGE_WALLETS.values():
            txs = await get_exchange_txns(wallets[0], limit=3)
            assert isinstance(txs, list)
            break

    @pytest.mark.asyncio
    async def test_exchange_flow_cycle_shape(self) -> None:
        result = await exchange_flow_cycle()
        assert "scanned_at" in result
        assert "exchanges" in result
        assert "total_exchange_balance" in result
        assert isinstance(result["exchanges"], list)
        assert len(result["exchanges"]) >= 5
        for e in result["exchanges"]:
            assert "exchange" in e
            assert "total_balance_eth" in e
            assert "wallets_monitored" in e
            assert "large_txns_24h" in e
