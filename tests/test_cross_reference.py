"""
Tests for cross_reference.py — historical token cross-reference.

Covers the Qdrant fallback path (LanceDB unavailable in the RAG venv),
narrative building, and address validation. Qdrant HTTP is mocked with respx.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx

from rugbrain.cross_reference import (
    _funds_from_metadata,
    build_narrative,
    find_similar_historical_tokens,
)

VALID_ADDRESS = "0x0a16a85be44627c10cee75db06b169c7bc76de2c"


def test_funds_from_metadata_variants() -> None:
    assert _funds_from_metadata({"amount_lost_usd": 1500000}) == 1500000.0
    assert _funds_from_metadata({"amount_lost": "$2.5M"}) == 2500000.0
    assert _funds_from_metadata({"amount_lost": "500K"}) == 500000.0
    assert _funds_from_metadata({}) == 0.0


async def test_find_similar_historical_tokens_invalid_address() -> None:
    result = await find_similar_historical_tokens("not-an-address")
    assert result["backend"] == "none"
    assert result["error"] == "invalid_address"
    assert result["similar_tokens"] == []


def test_build_narrative_no_tokens() -> None:
    text = build_narrative(VALID_ADDRESS, [])
    assert "No historical token" in text


def test_build_narrative_with_tokens() -> None:
    similar = [
        {
            "name": "AgaveExploit",
            "similarity": 0.85,
            "outcome": "exploited — lost ~$1,500,000",
            "date": "2022-03-01T00:00:00+00:00",
        }
    ]
    text = build_narrative(VALID_ADDRESS, similar)
    assert VALID_ADDRESS in text
    assert "AgaveExploit" in text
    assert "0.85" in text or "85%" in text


def test_build_narrative_with_matched_patterns() -> None:
    similar = [
        {
            "name": "AgaveExploit",
            "similarity": 0.8,
            "outcome": "exploited — lost ~$1,500,000",
        }
    ]
    patterns = [
        {
            "name": "reentrancy",
            "technique": "withdraw-after-external-call",
            "confidence": 0.9,
        }
    ]
    text = build_narrative(VALID_ADDRESS, similar, matched_patterns=patterns)
    assert "withdraw-after-external-call pattern" in text
    assert "90%" in text


async def test_find_similar_historical_tokens_qdrant_fallback(monkeypatch: pytest.MonkeyPatch) -> Any:
    """LanceDB is unavailable → Qdrant fallback path (mocked via respx)."""
    # Force the Qdrant fallback path deterministically instead of assuming
    # whether lancedb happens to be installed in the current environment.
    monkeypatch.setattr("rugbrain.cross_reference._import_lance_store", lambda: None)

    # Mock the embedding backend so no Qwen3/OpenRouter call escapes.
    async def fake_embedding(text: str) -> list[float]:
        return [0.1] * 1024

    monkeypatch.setattr("rugbrain.embeddings.get_embedding", fake_embedding)

    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        respx_mock.get("/collections/threat_intel_v2").respond(
            200,
            json={"result": {"config": {"params": {"vectors": {"dense": {"size": 1024}}}}}},
        )
        respx_mock.post("/collections/threat_intel_v2/points/search").respond(
            200,
            json={
                "result": [
                    {
                        "id": "doc1",
                        "score": 0.9,
                        "payload": {
                            "content": "reentrancy exploit code",
                            "metadata": {
                                "contract_name": "AgaveExploit",
                                "chain": "EVM",
                                "category": "reentrancy",
                                "amount_lost_usd": 1500000,
                                "source_url": "https://github.com/SunWeb3Sec/DeFiHackLabs",
                            },
                            "source": "defihacklabs",
                        },
                    }
                ]
            },
        )
        respx_mock.get("/collections/scams_v2").respond(
            200,
            json={"result": {"config": {"params": {"vectors": {"dense": {"size": 1024}}}}}},
        )
        respx_mock.post("/collections/scams_v2/points/search").respond(200, json={"result": []})

        result = await find_similar_historical_tokens(VALID_ADDRESS, chain="ethereum", limit=5)

    assert result["backend"] == "qdrant"
    assert result["similar_tokens"]
    assert result["similar_tokens"][0]["name"] == "AgaveExploit"
    assert result["similar_tokens"][0]["funds_lost_usd"] == 1500000.0


async def test_cross_reference_token_builds_narrative() -> Any:
    import rugbrain.cross_reference as cr

    async def fake_similar(address: str, chain: str, limit: int) -> Any:
        return [
            {
                "address": address,
                "chain": chain,
                "name": "AgaveExploit",
                "similarity": 0.9,
                "outcome": "exploited — lost ~$1,500,000",
                "date": "2022-03-01T00:00:00+00:00",
                "backend": "qdrant",
            }
        ]

    original = cr._qdrant_similar
    cr._qdrant_similar = fake_similar
    try:
        result = await cr.cross_reference_token(VALID_ADDRESS, chain="ethereum", limit=5)
    finally:
        cr._qdrant_similar = original

    assert result["backend"] == "qdrant"
    assert result["narrative"]
    assert "AgaveExploit" in result["narrative"]
