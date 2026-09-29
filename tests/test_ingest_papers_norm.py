"""Tests for the papers/standards ingest (EIPs + DeFiLlama protocol rows).

Pure-logic tests only — no network. The harvest functions are exercised in
``test_papers_harvest.py`` against ``respx`` mocks. What matters here is
the three properties that make a re-run safe: **deterministic point ids**,
**stub rejection**, and the **single-collection / 4096-lane declaration**.
"""

from __future__ import annotations

import pytest

from rugbrain.ingest_sources import papers_norm as pn

EIP_PATH = "EIPS/eip-4844.md"
EIP_URL = "https://raw.githubusercontent.com/ethereum/EIPs/master/EIPS/eip-4844.md"
EIP_BODY = (
    "---\n"
    "eip: 4844\n"
    "title: Sharding Optimizations\n"
    "status: Draft\n"
    "type: Standards Track\n"
    "category: Core\n"
    "created: 2023-05-19\n"
    "---\n\n"
    "## Abstract\n\nThis proposal restructures the blob gas schedule.\n\n"
    "## Motivation\n\n" + ("Rollup posting costs dominate the fee market. " * 30) + "\n"
)
STUB_BODY = (
    "---\neip: 20\ncategory: ERC\nstatus: Moved\n---\n\n"
    "This file was moved to https://github.com/ethereum/ercs/blob/master/ERCS/erc-20.md\n"
)


def test_point_id_is_deterministic() -> None:
    assert pn.point_id("eips", "4844") == pn.point_id("eips", "4844")


def test_point_id_differs_per_source_and_key() -> None:
    assert pn.point_id("eips", "4844") != pn.point_id("eips", "721")
    assert pn.point_id("eips", "4844") != pn.point_id("defillama", "4844")


def test_point_id_is_a_valid_qdrant_int() -> None:
    value = pn.point_id("eips", "4844")
    assert isinstance(value, int)
    assert 0 <= value < 2**63


def test_frontmatter_parses_scalars_only() -> None:
    meta = pn.frontmatter(EIP_BODY)
    assert meta["title"] == "Sharding Optimizations"
    assert meta["status"] == "Draft"
    assert meta["category"] == "Core"


def test_frontmatter_absent_returns_empty() -> None:
    assert pn.frontmatter("no front matter here") == {}


def test_is_stub_detects_moved_marker() -> None:
    assert pn.is_stub(STUB_BODY) is True


def test_is_stub_detects_tiny_file() -> None:
    assert pn.is_stub("x" * (pn.STUB_BYTES - 1)) is True


def test_is_stub_false_for_real_spec() -> None:
    assert pn.is_stub(EIP_BODY) is False


def test_parse_eip_normalizes_metadata() -> None:
    doc = pn.parse_eip(EIP_PATH, EIP_BODY, EIP_URL)
    assert doc is not None
    assert doc.source == "eips"
    assert doc.license == pn.EIPS_LICENSE
    assert doc.title == "Sharding Optimizations"
    assert doc.metadata["eip"] == 4844
    assert doc.metadata["status"] == "Draft"
    assert doc.metadata["collection"] == pn.COLLECTION
    assert doc.metadata["vector_size"] == 4096
    assert doc.metadata["embedding_lane"] == pn.EMBEDDING_LANE


def test_parse_eip_rejects_stub() -> None:
    assert pn.parse_eip("EIPS/eip-20.md", STUB_BODY, EIP_URL) is None


def test_parse_eip_rejects_non_eip_path() -> None:
    assert pn.parse_eip("README.md", EIP_BODY, EIP_URL) is None


def test_parse_eip_falls_back_to_synthetic_title() -> None:
    doc = pn.parse_eip(EIP_PATH, "body only, no front matter " + "x" * pn.STUB_BYTES, EIP_URL)
    assert doc is not None
    assert doc.title == "EIP-4844"


def test_protocol_doc_carries_address_primitive() -> None:
    row = {
        "name": "Uniswap V3",
        "slug": "uniswap-v3",
        "category": "Dexes",
        "address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        "chains": ["Ethereum", "Arbitrum"],
        "url": "https://defillama.com/protocol/uniswap-v3",
        "description": "Concentrated liquidity AMM.",
    }
    doc = pn.protocol_doc(row)
    assert doc is not None
    assert doc.metadata["address"] == row["address"]
    assert doc.metadata["category"] == "Dexes"
    assert doc.metadata["chains"] == ["Ethereum", "Arbitrum"]
    assert "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984" in doc.text


def test_protocol_doc_coerces_string_chain() -> None:
    doc = pn.protocol_doc({"name": "X", "slug": "x", "chains": "Ethereum"})
    assert doc is not None
    assert doc.metadata["chains"] == ["Ethereum"]


def test_protocol_doc_rejects_empty_slug() -> None:
    assert pn.protocol_doc({"name": "", "slug": ""}) is None


def test_protocol_doc_id_is_stable_across_calls() -> None:
    row = {"name": "Aave", "slug": "aave", "category": "Lending"}
    assert pn.protocol_doc(row).point_id == pn.protocol_doc(row).point_id  # type: ignore[union-attr]


def test_declares_single_collection_and_4096_lane() -> None:
    assert pn.COLLECTION == "papers_standards"
    assert pn.VECTOR_SIZE == 4096
    assert pn.EMBEDDING_LANE == "qwen3-embedding-8b"
    assert pn.DISTANCE == "Cosine"


def test_does_not_target_research_v2() -> None:
    """The 158 existing RSS rows must never be written by this module."""
    assert pn.COLLECTION != "research_v2"


@pytest.mark.parametrize("n", [1, 20, 4844, 99999])
def test_eip_path_re_matches(n: int) -> None:
    assert pn.EIP_PATH_RE.match(f"EIPS/eip-{n}.md")
