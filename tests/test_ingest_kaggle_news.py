"""Unit tests for Kaggle news ingest."""

from __future__ import annotations

import pathlib
import sys

import pytest

# Import the module to test
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

from ingest_kaggle_news import _stable_news_id, _chunk_text, _extract_entities


def test_stable_news_id():
    """Test news ID generation."""
    id1 = _stable_news_id("testsource", "http://example.com/article")
    id2 = _stable_news_id("testsource", "http://example.com/article")
    id3 = _stable_news_id("testsource", "http://example.com/other")

    assert id1 == id2  # Same input -> same ID
    assert id1 != id3  # Different URL -> different ID
    assert id1.startswith("kaggle:")


def test_chunk_text():
    """Test text chunking."""
    # Short text
    short = "This is a short text"
    chunks = _chunk_text(short, size=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0] == short

    # Long text
    long_text = "word " * 200  # ~1000 characters
    chunks = _chunk_text(long_text, size=100, overlap=20)
    assert len(chunks) > 1
    # Verify chunks are non-empty and within bounds
    for chunk in chunks:
        assert 0 < len(chunk) <= 100


def test_extract_entities():
    """Test entity extraction."""
    # Normal case
    entities = _extract_entities("Bitcoin, Ethereum, FTX")
    assert entities == ["BITCOIN", "ETHEREUM", "FTX"]

    # With extra spaces and duplicates
    entities = _extract_entities("  bitcoin  ,  ETHEREUM  , bitcoin  ,  solana  ")
    assert entities == ["BITCOIN", "ETHEREUM", "SOLANA"]

    # Empty/None
    assert _extract_entities(None) == []
    assert _extract_entities("") == []
    assert _extract_entities("   ") == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
