"""Tests for rugbrain.monitors.oracle_monitor."""

from __future__ import annotations

import pytest
from rugbrain.monitors.oracle_monitor import MAJOR_COINS


class TestOracleMonitor:
    def test_major_coins_defined(self):
        assert len(MAJOR_COINS) >= 8
        assert "BTC" in MAJOR_COINS
        assert "ETH" in MAJOR_COINS
        assert "SOL" in MAJOR_COINS

    def test_major_coins_no_duplicates(self):
        assert len(MAJOR_COINS) == len(set(MAJOR_COINS))

    def test_major_coins_all_uppercase(self):
        for coin in MAJOR_COINS:
            assert coin == coin.upper()

    @pytest.mark.asyncio
    async def test_oracle_cycle_structure(self):
        from rugbrain.monitors.oracle_monitor import oracle_scan_cycle

        try:
            result = await oracle_scan_cycle()
            assert "scanned_at" in result or "timestamp" in result
            assert "coins" in result
            assert "alerts" in result or "critical_events" in result or "total_events" in result
        except (ConnectionError, TimeoutError, OSError):
            pytest.skip("API unavailable")
