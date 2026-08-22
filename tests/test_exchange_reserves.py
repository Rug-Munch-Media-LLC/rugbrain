"""Tests for rugbrain.monitors.exchange_reserves."""

from __future__ import annotations

from rugbrain.monitors.exchange_reserves import get_all_reserves, get_reserves


class TestExchangeReserves:
    def test_get_all_reserves_returns_dict(self) -> None:
        result = get_all_reserves()
        assert isinstance(result, dict)
        # Should have exchange names as keys or be empty dict
        if result:
            name = next(iter(result.keys()))
            assert "btc" in result[name] or "last_verified" in result[name]

    def test_get_reserves_known_exchange(self) -> None:
        result = get_reserves("Binance")
        if result:
            assert "btc" in result or "last_verified" in result

    def test_get_reserves_unknown_exchange(self) -> None:
        result = get_reserves("NonExistentExchange123")
        assert result is None
