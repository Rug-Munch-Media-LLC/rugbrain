"""Tests for scripts/ingest_forta.py — Forta malicious contract ingestion."""

from __future__ import annotations

import asyncio
import pytest
import respx
import httpx

# Import the module under test
import sys
sys.path.insert(0, "scripts")
from ingest_forta import _parse_csv, _build_docs, run  # type: ignore


# ── Sample CSV data (matching the Forta schema) ──────────────────────────
SAMPLE_CSV = """contract_address,contract_tag,contract_creator,contract_creation_tx,contract_creator_tag,source,notes,contract_creator_etherscan_label
0x1234567890123456789012345678901234567890,exploit,0xabcdefabcdefabcdefabcdefabcdefabcdefabcd,0x1111111111111111111111111111111111111111111111111111111111111111,creator_tag,etherscan,"Malicious contract used in exploit",exploit_creator
0x0987654321098765432109876543210987654321,phish-hack,0xfedcbafedcbafedcbafedcbafedcbafedcba,0x2222222222222222222222222222222222222222222222222222222222222222,phishing_source,forta,"Phishing contract",phishing_label
"""

# Expected parsed entries
EXPECTED_ENTRIES = [
    {
        "address": "0x1234567890123456789012345678901234567890",
        "chain": "ethereum",
        "tag": "exploit",
        "vector": "exploit",
        "creator": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
        "creation_tx": "0x1111111111111111111111111111111111111111111111111111111111111111",
        "creator_tag": "creator_tag",
        "source": "etherscan",
        "notes": "Malicious contract used in exploit",
        "creator_label": "exploit_creator",
    },
    {
        "address": "0x0987654321098765432109876543210987654321",
        "chain": "ethereum",
        "tag": "phish-hack",
        "vector": "phishing",
        "creator": "0xfedcbafedcbafedcbafedcbafedcbafedcba",
        "creation_tx": "0x2222222222222222222222222222222222222222222222222222222222222222",
        "creator_tag": "phishing_source",
        "source": "forta",
        "notes": "Phishing contract",
        "creator_label": "phishing_label",
    },
]


def test_parse_csv_ethereum():
    entries = _parse_csv(SAMPLE_CSV, "ethereum")
    assert len(entries) == 2
    for i, entry in enumerate(entries):
        expected = EXPECTED_ENTRIES[i]
        for key, val in expected.items():
            assert entry[key] == val, f"mismatch at index {i} key {key}: {entry[key]} != {val}"


def test_parse_csv_optimism():
    # Same CSV but different chain
    entries = _parse_csv(SAMPLE_CSV, "optimism")
    assert len(entries) == 2
    for entry in entries:
        assert entry["chain"] == "optimism"


def test_parse_csv_skips_invalid_address():
    bad_csv = "contract_address,contract_tag\nnot_an_address,exploit\n"
    entries = _parse_csv(bad_csv, "ethereum")
    assert len(entries) == 0


def test_build_docs():
    entries = EXPECTED_ENTRIES
    docs = _build_docs(entries, limit=None)
    assert len(docs) == 2
    for i, doc in enumerate(docs):
        expected = EXPECTED_ENTRIES[i]
        assert doc["doc_id"] == f"forta:forta:ethereum:{expected['address']}"
        assert expected["address"] in doc["text"]
        assert doc["metadata"]["entity"] == expected["address"]
        assert doc["metadata"]["entity_type"] == "address"
        assert doc["metadata"]["chain"] == expected["chain"]
        assert doc["metadata"]["attack_vector"] == expected["vector"]
        assert doc["metadata"]["tag"] == expected["tag"]
        assert doc["metadata"]["source_type"] == "forta_malicious_contract"
        assert doc["severity"] == "critical"  # exploit -> critical


def test_build_docs_limit():
    entries = EXPECTED_ENTRIES
    docs = _build_docs(entries, limit=1)
    assert len(docs) == 1


# ── Integration test with mocked HTTP ────────────────────────────────────

@respx.mock
@pytest.mark.asyncio
async def test_run_dry_run():
    # Mock the two CSV URLs
    for chain, url in {"ethereum": "https://raw.githubusercontent.com/forta-network/labelled-datasets/main/labels/1/malicious_smart_contracts.csv",
                       "optimism": "https://raw.githubusercontent.com/forta-network/labelled-datasets/main/labels/10/malicious_smart_contracts.csv"}.items():
        respx.get(url).mock(return_value=httpx.Response(200, text=SAMPLE_CSV))

    # Mock embed_many to return dummy vectors
    from rugbrain import ingest_common
    original_embed = ingest_common.embed_many
    async def mock_embed(client, texts):
        return [[0.1] * 1024 for _ in texts]
    ingest_common.embed_many = mock_embed

    try:
        stats = await run(dry_run=True, limit=1)
    finally:
        ingest_common.embed_many = original_embed

    assert stats["dry_run"] is True
    assert stats["fetched"] == 4  # 2 entries * 2 chains
    assert stats["embedded"] == 2  # limited to 1 per chain? Actually limit applies to total entries across both chains.
    # The limit is applied in _build_docs after combining all entries, so total limited to 1.
    assert stats["upserted"] == 0
    assert stats["errors"] == 0
    assert stats["collection"] == "scam_intel_v2"
    assert stats["source"] == "forta"
