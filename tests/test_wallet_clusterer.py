"""Tests for rugbrain.wallet_clusterer — entity resolution."""

from __future__ import annotations

import pytest

from rugbrain.wallet_clusterer import (
    KNOWN_ENTITIES,
    resolve_wallet,
    cluster_scan_cycle,
)


class TestWalletClustererConfig:
    """Verify wallet clustering configuration."""

    def test_known_entities_defined(self):
        assert len(KNOWN_ENTITIES) >= 30
        assert "0x28c6c06298d514db089934071355e5743bf21d60" in KNOWN_ENTITIES
        assert "0x7a250d5630b4cf539739df2c5dacb4c659f2488d" in KNOWN_ENTITIES

    def test_entity_types(self):
        types = {e["type"] for e in KNOWN_ENTITIES.values()}
        assert "exchange" in types
        assert "dex" in types
        assert "protocol" in types
        assert "token" in types
        assert "market_maker" in types

    def test_entity_risk_levels(self):
        for addr, entity in KNOWN_ENTITIES.items():
            assert entity["risk"] in ("low", "medium", "high")
            assert entity["name"]

    @pytest.mark.asyncio
    async def test_resolve_known_wallet(self):
        result = await resolve_wallet("0x28c6c06298d514db089934071355e5743bf21d60")
        assert result["label"] == "Binance 1"
        assert result["type"] == "exchange"
        assert result["risk"] == "low"

    @pytest.mark.asyncio
    async def test_resolve_unknown_wallet(self):
        result = await resolve_wallet("0x0000000000000000000000000000000000000001")
        assert result["address"] == "0x0000000000000000000000000000000000000001"
        assert result["type"] == "unknown"

    @pytest.mark.asyncio
    async def test_cluster_scan_cycle_shape(self):
        result = await cluster_scan_cycle()
        assert "scanned_at" in result
        assert "known_entities" in result
        assert "cached_resolutions" in result
        assert result["known_entities"] == len(KNOWN_ENTITIES)
