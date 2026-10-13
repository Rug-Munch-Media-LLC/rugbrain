"""Tests for the RugBrain MCP server."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The MCP facade needs the [mcp] extra: pip install -e ".[mcp]"
pytest.importorskip("fastmcp", reason="install the [mcp] extra")

from rugbrain.rugbrain_mcp.server import (
    rugbrain_market_brief,
    rugbrain_scam_radar,
    rugbrain_social_sentiment,
)


def _parse_tool_result(result):
    """Extract a tool payload from a ToolResult.

    Prefer ``structured_content`` — that is the contract FastMCP publishes to
    callers (it is what becomes the tool's JSON output) — and fall back to
    parsing the text block, which is what the older tools still emit.
    """
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and structured:
        return structured
    if not result.content:
        return {}
    text = result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


@pytest.mark.anyio
async def test_rugbrain_market_brief_stubbed():
    """Test rugbrain_market_brief with stubbed HTTP call."""
    # Mock the HTTP response
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "results": [
            {
                "content": "Bitcoin price surges",
                "score": 0.95,
                "_source_collections": ["news_v2"],
            },
            {
                "content": "Ethereum gas fees drop",
                "score": 0.87,
            },
        ],
        "query": "crypto",
        "limit": 2,
        "total": 2,
        "reranker_applied": True,
    }

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        # Call the tool
        result = await rugbrain_market_brief(query="crypto", limit=2)
        # The tool returns a ToolResult
        assert not result.is_error
        data = _parse_tool_result(result)

        # Check the structure
        assert "brief" in data
        assert isinstance(data["brief"], list)
        assert len(data["brief"]) == 2
        # Each item should have content, collection, score
        for item in data["brief"]:
            assert "content" in item
            assert "collection" in item
            assert "score" in item
            # The collection should be one of the three we searched
            assert item["collection"] in ["news_v2", "social_intel", "education"]
        # Check that the query and limit are echoed back
        assert data["query"] == "crypto"
        assert data["limit"] == 2

        # Verify that the HTTP call was made three times (once per collection)
        assert mock_client.return_value.__aenter__.return_value.post.call_count == 3


@pytest.mark.anyio
async def test_rugbrain_sentiment_stubbed():
    """Test rugbrain_sentiment with stubbed HTTP call."""
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "results": [
            {
                "content": "Positive sentiment around BTC",
                "score": 0.92,
                "_source_collections": ["social_intel"],
            }
        ],
        "query": "BTC",
        "limit": 1,
        "total": 1,
        "reranker_applied": True,
    }

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await rugbrain_social_sentiment(query="BTC", limit=1)
        assert not result.is_error
        data = _parse_tool_result(result)
        assert "results" in data
        assert isinstance(data["results"], list)
        assert len(data["results"]) == 1
        item = data["results"][0]
        assert "content" in item
        assert "collection" in item
        assert "score" in item
        assert item["collection"] in ["social_intel", "news_v2"]
        assert data["query"] == "BTC"
        assert data["limit"] == 1

        # ONE call to /api/v1/search/multi carrying both collections: the
        # endpoint fans out internally and RRF-fuses the results. Two calls
        # would forfeit the fusion and double the round trips.
        post = mock_client.return_value.__aenter__.return_value.post
        assert post.call_count == 1
        sent = post.call_args.kwargs["json"]
        assert sent["collections"] == ["social_intel", "news_v2"]
        assert sent["rerank"] is True


@pytest.mark.anyio
async def test_rugbrain_scam_radar_stubbed():
    """Test rugbrain_scam_radar with stubbed HTTP call."""
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "results": [
            {
                "content": "Scam token 0x1234567890123456789012345678901234567890 detected",
                "score": 0.99,
            }
        ],
        "query": "scam",
        "limit": 1,
        "total": 1,
        "reranker_applied": True,
    }

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        result = await rugbrain_scam_radar(query="scam", limit=1)
        assert not result.is_error
        data = _parse_tool_result(result)
        assert "results" in data
        assert isinstance(data["results"], list)
        assert len(data["results"]) == 1
        item = data["results"][0]
        assert "content" in item
        assert "collection" in item
        assert "score" in item
        assert "addresses" in item
        assert item["collection"] in ["scam_patterns_8b", "scam_intel_v2", "threat_intel_v2"]
        # Check that the address was extracted
        assert isinstance(item["addresses"], list)
        assert len(item["addresses"]) == 1
        assert item["addresses"][0] == "0x1234567890123456789012345678901234567890"
        assert data["query"] == "scam"
        assert data["limit"] == 1

        # Expect 3 calls
        assert mock_client.return_value.__aenter__.return_value.post.call_count == 3


@pytest.mark.anyio
async def test_rugbrain_market_brief_empty_result():
    """Test rugbrain_market_brief when a collection returns zero results."""
    # First collection returns results, second returns empty, third returns results
    def mock_post(*args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        data = kwargs.get("json", {})
        collections = data.get("collections", [])
        if collections == ["news_v2"]:
            mock_resp.json.return_value = {
                "results": [{"content": "news item", "score": 0.8}],
                "query": data.get("query", ""),
                "limit": data.get("limit", 10),
                "total": 1,
                "reranker_applied": data.get("rerank", False),
            }
        elif collections == ["social_intel"]:
            mock_resp.json.return_value = {
                "results": [],
                "query": data.get("query", ""),
                "limit": data.get("limit", 10),
                "total": 0,
                "reranker_applied": data.get("rerank", False),
            }
        elif collections == ["education"]:
            mock_resp.json.return_value = {
                "results": [{"content": "education item", "score": 0.7}],
                "query": data.get("query", ""),
                "limit": data.get("limit", 10),
                "total": 1,
                "reranker_applied": data.get("rerank", False),
            }
        else:
            mock_resp.json.return_value = {
                "results": [],
                "query": data.get("query", ""),
                "limit": data.get("limit", 10),
                "total": 0,
                "reranker_applied": data.get("rerank", False),
            }
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=mock_post)

        result = await rugbrain_market_brief(query="test", limit=5)
        assert not result.is_error
        data = _parse_tool_result(result)
        assert "brief" in data
        assert len(data["brief"]) == 2  # Only two collections had results
        # Check that the empty collection (social_intel) is not present in the results
        collections_in_result = {item["collection"] for item in data["brief"]}
        assert "social_intel" not in collections_in_result
        assert "news_v2" in collections_in_result
        assert "education" in collections_in_result
        # Scores should be present
        for item in data["brief"]:
            assert "score" in item
            assert isinstance(item["score"], float)
        assert data["query"] == "test"
        assert data["limit"] == 5

        # Expect 3 calls
        assert mock_client.return_value.__aenter__.return_value.post.call_count == 3


if __name__ == "__main__":
    pytest.main([__file__])
