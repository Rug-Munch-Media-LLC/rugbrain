"""Tests for rugbrain.token_unlock_monitor."""

from __future__ import annotations

import pytest

from rugbrain.token_unlock_monitor import (
    TOKEN_UNLOCKS,
    unlock_scan_cycle,
)


class TestTokenUnlockConfig:
    """Verify token unlock schedule data."""

    def test_token_unlocks_defined(self) -> None:
        assert len(TOKEN_UNLOCKS) >= 15
        tokens = {u["token"] for u in TOKEN_UNLOCKS}
        assert "APT" in tokens
        assert "ARB" in tokens
        assert "OP" in tokens
        assert "TIA" in tokens
        assert "W" in tokens

    def test_token_unlocks_have_required_fields(self) -> None:
        for u in TOKEN_UNLOCKS:
            assert "token" in u
            assert "date" in u
            assert "amount" in u
            assert "type" in u
            assert u["type"] in ("cliff", "linear")

    def test_unlock_dates_parsable(self) -> None:
        from datetime import datetime
        for u in TOKEN_UNLOCKS:
            dt = datetime.fromisoformat(u["date"])
            assert dt.year >= 2025

    def test_unlock_amounts_positive(self) -> None:
        for u in TOKEN_UNLOCKS:
            assert u["amount"] > 0


class TestTokenUnlockCycle:
    """Test the unlock scan cycle."""

    @pytest.mark.asyncio
    async def test_scan_cycle_shape(self) -> None:
        result = await unlock_scan_cycle()
        assert "scanned_at" in result
        assert "total_unlocks" in result
        assert "imminent_unlocks" in result
        assert "upcoming_cliffs" in result
        assert "unlocks" in result
        assert "imminent_list" in result
        assert "cliff_alerts" in result
        assert isinstance(result["total_unlocks"], int)
        assert isinstance(result["unlocks"], list)
        assert len(result["unlocks"]) > 0

    @pytest.mark.asyncio
    async def test_scan_cycle_sorted_by_date(self) -> None:
        result = await unlock_scan_cycle()
        unlocks = result["unlocks"]
        for i in range(len(unlocks) - 1):
            assert unlocks[i]["days_until"] <= unlocks[i + 1]["days_until"]

    @pytest.mark.asyncio
    async def test_imminent_unlocks_filtered(self) -> None:
        result = await unlock_scan_cycle()
        for u in result["imminent_list"]:
            assert u["is_imminent"] is True
            assert u["days_until"] <= 30

    @pytest.mark.asyncio
    async def test_cliff_alerts_filtered(self) -> None:
        result = await unlock_scan_cycle()
        for u in result["cliff_alerts"]:
            assert u["is_cliff"] is True
            assert u["days_until"] <= 60
