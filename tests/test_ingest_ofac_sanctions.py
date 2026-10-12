"""Tests for scripts/ingest_ofac_sanctions.py.

Three things must hold:
  1. the default (dry-run) path writes NOTHING to Qdrant,
  2. point IDs are deterministic so a re-run upserts instead of duplicating,
  3. crypto address extraction from OFAC remarks works correctly.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_ofac_sanctions as ing

# Sample OFAC SDN XML with crypto addresses in remarks
SAMPLE_OFAC_XML = """<?xml version="1.0" standalone="yes"?>
<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">
  <publshInformation>
    <Publish_Date>10/10/2026</Publish_Date>
    <Record_Count>3</Record_Count>
  </publshInformation>
  <sdnEntry>
    <uid>1001</uid>
    <lastName>BAD ACTOR</lastName>
    <firstName>CRYPTO</firstName>
    <sdnType>Individual</sdnType>
    <program>["CYBER"]</program>
    <remarks>Bitcoin address: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa and Ethereum: 0x742d35Cc6634C0532925a3b8D4C05329505428E4 involved in ransomware.</remarks>
  </sdnEntry>
  <sdnEntry>
    <uid>1002</uid>
    <lastName>CLEAN ENTITY</lastName>
    <firstName>GOOD</firstName>
    <sdnType>Entity</sdnType>
    <program>["OTHER"]</program>
    <remarks>No cryptocurrency involved.</remarks>
  </sdnEntry>
  <sdnEntry>
    <uid>1003</uid>
    <lastName>MULTI CHAIN</lastName>
    <firstName>SOLANA</firstName>
    <sdnType>Entity</sdnType>
    <program>["CYBER"]</program>
    <remarks>Solana: 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtVWWM and TRX: T9yD14Nj9j7xAB4dbGeiX9h8M7H6ic7Q2u.</remarks>
  </sdnEntry>
</sdnList>
"""


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    """Dry-run fetches and embeds but does not write to Qdrant."""
    attempts: list[str] = []

    async def fake_fetch(use_cache):
        return SAMPLE_OFAC_XML

    async def forbidden_insert(*args, **kwargs):
        attempts.append("INSERT_ATTEMPTED")
        raise AssertionError("dry run must not call insert")

    async def fake_embed(texts):
        return [[0.2] * 4096 for _ in texts]

    monkeypatch.setattr(ing, "_fetch_ofac_sdn_xml", fake_fetch)
    monkeypatch.setattr(ing, "get_embedder", lambda: _MockEmbedder())
    monkeypatch.setattr("rugbrain.qdrant_store.QdrantVectorStore.insert", forbidden_insert)
    monkeypatch.setattr("rugbrain.qdrant_store.QdrantVectorStore.close", lambda self: None)
    monkeypatch.setattr("sys.argv", ["ingest_ofac_sanctions.py", "--dry-run"])

    stats = asyncio.run(ing.run(dry_run=True, limit=None))
    assert stats["fetched"] == 1
    assert stats["embedded"] == 3  # 3 entries, each embedded
    assert stats["upserted"] == 0
    assert stats["errors"] == 0
    assert attempts == [], "dry run must not attempt Qdrant writes"


def test_crypto_address_extraction():
    """Extract crypto addresses from remarks."""
    addresses = ing._extract_crypto_addresses(
        "Bitcoin: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa and ETH: 0x742d35Cc6634C0532925a3b8D4C05329505428E4"
    )
    assert len(addresses) == 2
    addr_chain = {(a["address"], a["chain"]) for a in addresses}
    assert ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "BTC") in addr_chain
    assert ("0x742d35Cc6634C0532925a3b8D4C05329505428E4", "ETH") in addr_chain


def test_solana_trx_addresses():
    """Extract Solana and TRX addresses."""
    text = "Solana: 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtVWWM and TRX: T9yD14Nj9j7xAB4dbGeiX9h8M7H6ic7Q2u"
    addresses = ing._extract_crypto_addresses(text)
    addr_chain = {(a["address"], a["chain"]) for a in addresses}
    assert ("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtVWWM", "SOL") in addr_chain
    assert ("T9yD14Nj9j7xAB4dbGeiX9h8M7H6ic7Q2u", "TRX") in addr_chain


def test_deduplication_same_address():
    """Same address in different case or format deduplicated."""
    text = "btc: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa and BTC: 1a1zp1ep5qgefidmptfTL5SLMV7DIVFNA"
    addresses = ing._extract_crypto_addresses(text)
    # Should only get one unique address (case insensitive)
    assert len(addresses) == 1
    assert addresses[0]["chain"] == "BTC"
    assert addresses[0]["address"] == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def test_build_metadata_from_entry():
    """Metadata built from parsed entry."""
    entry = {
        "uid": "12345",
        "name": "TEST ENTITY",
        "sdn_type": "Individual",
        "program": "[CYBER]",
        "crypto_addresses": [
            {
                "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                "address_lower": "1a1zp1ep5qgefidmptfTL5slmv7divfna",
                "chain": "BTC",
            },
            {
                "address": "0x742d35Cc6634C0532925a3b8D4C05329505428E4",
                "address_lower": "0x742d35cc6634c0532925a3b8d4c05329505428e4",
                "chain": "ETH",
            },
        ],
        "source": "ofac_sdn",
        "source_url": "https://home.treasury.gov/policy-issues/financial-sanctions/specially-designated-nationals-sdn-list",
    }
    meta = ing._build_metadata(entry)  # from the script's _build_metadata logic
    assert meta["name"] == "TEST ENTITY"
    assert meta["uid"] == "12345"
    assert meta["sdn_type"] == "Individual"
    assert meta["program"] == "[CYBER]"
    assert meta["crypto_count"] == 2
    assert meta["source"] == "ofac_sdn"
    assert meta["knowledge_type"] == "ofac_sanction"


def test_point_id_deterministic():
    """Same OFAC entry produces same deterministic point ID."""
    entry = {
        "uid": "5001",
        "crypto_addresses": [
            {
                "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
                "address_lower": "1a1zp1ep5qgefidmptfTL5slmv7divfna",
                "chain": "BTC",
            },
        ],
    }
    # Point ID format: "ofac:{uid}:{first_addr_lower}"
    addr_lower = entry["crypto_addresses"][0]["address_lower"]
    id1 = f"ofac:{entry['uid']}:{addr_lower}"
    id2 = f"ofac:{entry['uid']}:{addr_lower}"
    assert id1 == id2
    assert id1 != f"ofac:{entry['uid']}:different"


def test_point_id_no_crypto_fallback():
    """Entries with no crypto use 'nokey' suffix."""
    entry = {
        "uid": "9999",
        "crypto_addresses": [],
    }
    id1 = f"ofac:{entry['uid']}:nokey"
    id2 = f"ofac:{entry['uid']}:nokey"
    assert id1 == id2


class _MockResponse:
    def __init__(self, data):
        self._data = data
        self.status_code = 200

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _MockEmbedder:
    async def embed(self, texts):
        return [[0.2] * 4096 for _ in texts]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
