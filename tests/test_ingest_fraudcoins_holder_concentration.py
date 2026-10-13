#!/usr/bin/env python3
"""Test for ingest_fraudcoins_holder_concentration."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add the scripts directory to sys.path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from ingest_fraudcoins_holder_concentration import (
    build_summary,
    content_hash,
    split_text,
    to_uuid,
)

def test_to_uuid():
    """Test UUID generation."""
    doc_id = "test:123:abc"
    uuid_str = to_uuid(doc_id)
    assert isinstance(uuid_str, str)
    assert len(uuid_str) == 36  # standard UUID length
    # Deterministic
    assert to_uuid(doc_id) == uuid_str

def test_content_hash():
    """Test content hash."""
    text = "hello world"
    hash1 = content_hash(text)
    hash2 = content_hash(text)
    assert hash1 == hash2
    assert len(hash1) == 12  # first 12 chars of sha256
    assert hash1 != content_hash("different")

def test_split_text():
    """Test text splitting."""
    short_text = "short"
    assert split_text(short_text) == ["short"]
    
    long_text = "a" * 1500
    chunks = split_text(long_text, max_chars=1000)
    assert len(chunks) == 2
    assert len(chunks[0]) == 1000
    assert len(chunks[1]) == 500
    assert chunks[0] + chunks[1] == long_text

def test_build_summary():
    """Test summary building."""
    summary = build_summary(
        coin_id="btc",
        symbol="BTC",
        name="Bitcoin",
        top10_pct=85.7,
        date_str="2026-08-31"
    )
    assert "BTC" in summary
    assert "Bitcoin" in summary
    assert "85.7%" in summary
    assert "2026-08-31" in summary
    assert "whale/insider risk" in summary

def test_build_summary_edge_cases():
    """Test edge cases."""
    # Zero percent
    summary = build_summary("x", "X", "Xcoin", 0.0, "2026-01-01")
    assert "0.0%" in summary
    # Empty strings
    summary = build_summary("", "", "", 0.0, "")
    assert "Token   " in summary  # still forms a sentence

@pytest.mark.asyncio
async def test_main_logic_with_mocks():
    """Test the main logic with mocked dependencies."""
    # We'll test the main function by mocking the external calls
    from ingest_fraudcoins_holder_concentration import main
    
    # Mock the file loading
    mock_concentration = {
        "generatedAt": "2026-10-12T04:13:08.969Z",
        "thresholdPct": 50,
        "minMarketCap": 50000000,
        "tiers": [],
        "minSampleToFlag": 5,
        "methodology": "test",
        "coins": {
            "btc": {
                "chain": "bitcoin",
                "address": "0x1",
                "top10Pct": 85.7,
                "flagged": False,
                "neverFlag": True,
                "contractsInTop10": 1,
                "rawTop10Pct": 85.7,
                "tier": "daily",
                "flagThreshold": 50,
                "excludedFromTop10": 0,
                "holdersSampled": 99,
                "holdersFetched": 100,
                "sampledAt": "2026-08-31T06:27:35.886Z"
            }
        }
    }
    mock_history = {
        "coins": {
            "btc": [
                ["2026-08-01", 80.0],
                ["2026-08-31", 85.7]
            ]
        }
    }
    mock_coins_index = [
        {"id": "btc", "name": "Bitcoin", "symbol": "BTC"}
    ]
    
    with patch("builtins.open", side_effect=[
        MagicMock(__enter__=MagicMock(return_value=MagicMock(__iter__=MagicMock(return_value=iter([json.dumps(mock_concentration)]))))),
        MagicMock(__enter__=MagicMock(return_value=MagicMock(__iter__=MagicMock(return_value=iter([json.dumps(mock_history)]))))),
        MagicMock(__enter__=MagicMock(return_value=MagicMock(__iter__=MagicMock(return_value=iter([json.dumps(mock_coins_index)])))))
    ]):
        # Mock embedder and store
        with patch("ingest_fraudcoins_holder_concentration.get_embedder") as mock_get_embedder, \
             patch("ingest_fraudcoins_holder_concentration.QdrantVectorStore") as mock_store_class:
            
            mock_embedder = AsyncMock()
            mock_embedder.embed.return_value = [[0.1] * 4096]  # dummy vector
            mock_get_embedder.return_value = mock_embedder
            
            mock_store = AsyncMock()
            mock_store_class.return_value = mock_store
            
            # Run main with limit=1, dry_run=True
            result = await main(limit=1, dry_run=True, batch_size=100)
            
            # Check stats
            assert result["coins_processed"] == 1
            assert result["summaries_embedded"] == 1
            assert result["parquet_rows_written"] == 2
            assert result["skipped"] == 0
            assert result["errors"] == 0

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
