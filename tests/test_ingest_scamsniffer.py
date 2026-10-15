"""Tests for scripts/ingest_scamsniffer.py — ScamSniffer phishing feed ingestion."""

from __future__ import annotations

import asyncio
import pytest
import respx
import httpx

# Import the module under test
import sys
sys.path.insert(0, "scripts")
from ingest_scamsniffer import _domain_docs, _address_docs, run  # type: ignore


# ── Sample JSON data ─────────────────────────────────────────────────────
SAMPLE_DOMAINS = [
    "ethamg.top",
    "claims-unibase.com",
    "multisolfix.xyz",
]

SAMPLE_ADDRESSES = [
    "0x101ce0cedd142f199c9ef61739ae59b6611a0fc0",
    "0x43412801d29861ecc4c4d86e5becfd16af86a67b",
]


def test_domain_docs():
    docs = _domain_docs(SAMPLE_DOMAINS, limit=None)
    assert len(docs) == 3
    for i, doc in enumerate(docs):
        domain = SAMPLE_DOMAINS[i].lower()
        assert doc["doc_id"] == f"scamsniffer:domain:{domain}"
        assert domain in doc["text"]
        assert doc["metadata"]["entity"] == domain
        assert doc["metadata"]["entity_type"] == "domain"
        assert doc["metadata"]["attack_vector"] == "phishing"
        assert doc["metadata"]["source"] == "scamsniffer"
        assert doc["metadata"]["source_url"] == "https://github.com/scamsniffer/scam-database"
        assert doc["metadata"]["source_type"] == "scamsniffer_domain"
        assert doc["severity"] == "high"
        assert doc["chain"] == "multi"
        assert doc["collection"] == "threat_intel_v2_8b"


def test_domain_docs_limit():
    docs = _domain_docs(SAMPLE_DOMAINS, limit=2)
    assert len(docs) == 2


def test_address_docs():
    docs = _address_docs(SAMPLE_ADDRESSES, limit=None)
    assert len(docs) == 2
    for i, doc in enumerate(docs):
        addr = SAMPLE_ADDRESSES[i].lower()
        assert doc["doc_id"] == f"scamsniffer:address:{addr}"
        assert addr in doc["text"]
        assert doc["metadata"]["entity"] == addr
        assert doc["metadata"]["entity_type"] == "address"
        assert doc["metadata"]["attack_vector"] == "phishing"
        assert doc["metadata"]["source"] == "scamsniffer"
        assert doc["metadata"]["source_url"] == "https://github.com/scamsniffer/scam-database"
        assert doc["metadata"]["source_type"] == "scamsniffer_address"
        assert doc["severity"] == "high"
        assert doc["chain"] == "multi"
        assert doc["collection"] == "known_scams_8b"


def test_address_docs_limit():
    docs = _address_docs(SAMPLE_ADDRESSES, limit=1)
    assert len(docs) == 1


# ── Integration test with mocked HTTP ────────────────────────────────────

@respx.mock
@pytest.mark.asyncio
async def test_run_dry_run():
    # Mock the JSON URLs
    respx.get("https://raw.githubusercontent.com/scamsniffer/scam-database/main/blacklist/domains.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_DOMAINS)
    )
    respx.get("https://raw.githubusercontent.com/scamsniffer/scam-database/main/blacklist/address.json").mock(
        return_value=httpx.Response(200, json=SAMPLE_ADDRESSES)
    )

    # 2026-10-15: the embed seam is get_embedder().embed (embed_many is gone).
    from unittest.mock import AsyncMock, patch

    mock_embedder = AsyncMock()
    mock_embedder.embed.side_effect = lambda texts: [[0.1] * 4096 for _ in texts]
    with patch("ingest_scamsniffer.get_embedder", return_value=mock_embedder):
        stats = await run(dry_run=True, limit=2)

    assert stats["dry_run"] is True
    assert stats["domains_fetched"] == 3
    assert stats["addresses_fetched"] == 2
    assert stats["domains_upserted"] == 2  # limited to 2 per feed
    assert stats["addresses_upserted"] == 2
    assert stats["errors"] == 0
    assert stats["source"] == "scamsniffer"
    assert stats["domains_collection"] == "threat_intel_v2_8b"
    assert stats["addresses_collection"] == "known_scams_8b"
