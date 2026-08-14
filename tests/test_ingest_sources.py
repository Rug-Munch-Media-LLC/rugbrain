"""Tests for rugbrain.ingest_sources — programmatic top-tier crypto sources."""

from __future__ import annotations

import respx

from rugbrain import ingest_sources as mod
from rugbrain.ingest_sources import base, defihacklabs, defillama, eips, rekt, swc, whitepapers

# Every requested source must be registered with the right semantic type.
REQUIRED = {
    "defihacklabs": "exploit_poc",
    "swc_registry": "vulnerability_pattern",
    "eips": "standard",
    "bips": "standard",
    "openzeppelin_contracts": "contract_code",
    "rekt_news": "post_mortem",
    "crypto_whitepapers": "whitepaper",
    "defillama": "market_data",
}


def test_registry_contains_all_required_sources():
    names = mod.list_sources()
    assert set(REQUIRED) <= set(names), f"missing: {set(REQUIRED) - set(names)}"
    assert len(names) == len(set(names))  # no duplicates


def test_doc_types_assigned():
    for name, doc_type in REQUIRED.items():
        src = mod.get_source(name)
        assert src is not None
        assert src.doc_type == doc_type, f"{name}: expected {doc_type}, got {src.doc_type}"


def test_get_source_unknown():
    assert mod.get_source("does_not_exist") is None


def test_ingest_doc_shape():
    doc = base.IngestDoc(content="x", doc_id="a:1", metadata={"k": "v"}, doc_type="t", source="s")
    assert doc.content == "x"
    assert doc.doc_id == "a:1"
    assert doc.metadata == {"k": "v"}
    assert doc.doc_type == "t"
    assert doc.source == "s"
    assert doc.title == ""
    assert doc.url == ""


def test_to_crawl_document_bridge():
    doc = base.IngestDoc(
        content="body",
        doc_id="x:1",
        metadata={"collection": "threat_intel", "chain": "EVM"},
        doc_type="exploit_poc",
        source="defihacklabs",
        title="T",
        url="https://x/y",
    )
    crawl = base.to_crawl_document(doc)
    assert crawl.content == "body"
    assert crawl.collection == "threat_intel"
    assert crawl.category == "exploit_poc"
    assert crawl.metadata["doc_type"] == "exploit_poc"


# ── DeFiHackLabs parsers ────────────────────────────────────────────────


def test_defihacklabs_parse_sol():
    code = "// Reentrancy attack PoC\n// steals all funds\ncontract Hack { function pwn() external {} }"
    doc = defihacklabs.parse_sol_file("src/test/2023-01/Foo_exp.sol", code)
    assert doc is not None
    assert doc.doc_type == "exploit_poc"
    assert doc.metadata["category"] == "reentrancy"
    assert doc.metadata["severity"] == "critical"
    assert doc.metadata["contract_name"] == "Hack"
    assert "Reentrancy attack PoC" in doc.content
    assert doc.source == "defihacklabs"


def test_defihacklabs_parse_sol_empty():
    assert defihacklabs.parse_sol_file("src/a.sol", "   ") is None


def test_defihacklabs_parse_incident_readme():
    md = """# Past hacks

### 20230101 Foo Protocol - Reentrancy
### Lost: $10,000,000
### About
Foo got drained.

### 20220202 Bar - Oracle Manipulation
### Lost: $5M
"""
    docs = defihacklabs.parse_incident_readme("past/2023/README.md", md)
    assert len(docs) == 2
    assert docs[0].metadata["protocol"] == "Foo Protocol"
    assert docs[0].metadata["amount_lost"] == "$10,000,000"
    assert docs[1].metadata["attack_type"] == "Oracle Manipulation"
    assert docs[0].doc_type == "exploit_poc"


# ── SWC Registry parsers ────────────────────────────────────────────────


def test_swc_markdown_to_doc():
    md = """# Title

SWC-101

## Description
Integer overflow can cause unexpected values.

## Remediation
Use SafeMath.

## Relationships
- CWE-682
- CWE-190

```solidity
function transfer() { }
```
"""
    doc = swc.swc_markdown_to_doc("SWC-101", md)
    assert doc is not None
    assert doc.doc_type == "vulnerability_pattern"
    assert doc.metadata["swc_id"] == "SWC-101"
    assert doc.metadata["cwe"] == ["CWE-682", "CWE-190"]
    assert doc.metadata["has_sample_code"] is True
    assert "Integer overflow" in doc.content


def test_swc_markdown_empty_rejected():
    assert swc.swc_markdown_to_doc("SWC-999", "# Title\n\nnothing here") is None


# ── EIPs / BIPs parsers ─────────────────────────────────────────────────


def test_eips_parse_frontmatter():
    md = """---
title: ERC-20 Token Standard
status: Final
type: Standards Track
category: ERC
created: 2015-11-09
---

# Abstract
...
"""
    fields, body = eips.parse_frontmatter(md)
    assert fields["title"] == "ERC-20 Token Standard"
    assert fields["status"] == "Final"
    assert "Abstract" in body


def test_standard_to_doc():
    doc = eips.standard_to_doc(
        prefix="EIP",
        number="20",
        title="ERC-20",
        body="body",
        source="eips",
        fields={"status": "Final", "type": "Standards Track", "category": "ERC"},
        url="https://github.com/ethereum/EIPs/blob/master/EIPS/eip-20.md",
    )
    assert doc.doc_type == "standard"
    assert doc.metadata["standard_id"] == "EIP-20"
    assert doc.metadata["status"] == "Final"
    assert doc.title.startswith("EIP-20")


# ── Rekt news parsers ───────────────────────────────────────────────────


def test_rekt_parse_leaderboard():
    html = """
    <html><body>
    <a href="/poly-network-rekt/" class="x">Poly Network</a>
    <a href="/wormhole-rekt/">Wormhole</a>
    <a href="/poly-network-rekt/">Poly Network (dup)</a>
    </body></html>
    """
    articles = rekt.parse_leaderboard(html)
    assert len(articles) == 2
    assert articles[0].title == "Poly Network"
    assert articles[0].url == "/poly-network-rekt/"
    assert articles[1].title == "Wormhole"


def test_rekt_article_to_doc():
    doc = rekt.article_to_doc(rekt.RektArticle(rank=1, title="Wormhole", url="/wormhole-rekt/"))
    assert doc.doc_type == "post_mortem"
    assert doc.url == "https://rekt.news/wormhole-rekt/"
    assert doc.metadata["rank"] == 1


# ── Whitepapers parsers ─────────────────────────────────────────────────


def test_whitepapers_markdown_to_doc():
    md = "# Bitcoin: A Peer-to-Peer Electronic Cash System\n\nAbstract...\n"
    doc = whitepapers.markdown_to_doc("bitcoin.md", md)
    assert doc is not None
    assert doc.doc_type == "whitepaper"
    assert doc.title == "Bitcoin: A Peer-to-Peer Electronic Cash System"
    assert doc.metadata["format"] == "markdown"


def test_whitepapers_pdf_stub():
    doc = whitepapers.pdf_to_stub("ethereum.pdf", size=500_000)
    assert doc.doc_type == "whitepaper"
    assert doc.metadata["format"] == "pdf"
    assert doc.metadata["pending_extraction"] is True


# ── DeFiLlama parsers ───────────────────────────────────────────────────


def test_defillama_tvl_to_docs():
    chains = [
        {"name": "Ethereum", "tvl": 50000000000.0, "change_24h": 1.5},
        {"name": "Solana", "tvl": 0},
    ]
    docs = defillama.tvl_to_docs(chains)
    assert len(docs) == 2
    assert docs[0].doc_type == "market_data"
    assert docs[0].metadata["chain"] == "ethereum"
    assert docs[0].metadata["tvl_usd"] == 50000000000.0


def test_defillama_protocol_to_doc():
    proto = {
        "name": "Uniswap",
        "slug": "uniswap",
        "tvl": 4000000000.0,
        "revenue_24h": 100000.0,
        "chainTvls": {"Ethereum": 3000000000.0, "Arbitrum": 1000000000.0},
    }
    doc = defillama.protocol_to_doc(proto)
    assert doc is not None
    assert doc.doc_type == "market_data"
    assert doc.metadata["protocol"] == "Uniswap"
    assert "Arbitrum" in doc.content


def test_defillama_hacks_to_docs():
    hacks = [
        {
            "name": "Foo",
            "date": 1700000000,
            "amount": 5000000.0,
            "technique": "Reentrancy",
            "classification": "bug",
            "chain": ["Ethereum"],
            "returnedFunds": True,
        }
    ]
    docs = defillama.hacks_to_docs(hacks)
    assert len(docs) == 1
    assert docs[0].metadata["amount_lost_usd"] == 5000000.0
    assert docs[0].metadata["chain"] == "ethereum"
    assert docs[0].metadata["date"] == "2023-11-14"


# ── Fetch paths (mocked network) ────────────────────────────────────────


def _tree_payload(*paths: str) -> dict:
    return {"tree": [{"path": p, "type": "blob", "size": 100} for p in paths]}


@respx.mock
async def test_swc_fetch_end_to_end():
    swc_mod = mod.get_source("swc_registry")
    assert swc_mod is not None
    respx.get(
        "https://api.github.com/repos/SmartContractSecurity/SWC-registry/git/trees/master"
    ).respond(200, json=_tree_payload("entries/docs/SWC-100.md", "entries/docs/SWC-101.md"))
    respx.get(
        "https://raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/entries/docs/SWC-100.md"
    ).respond(200, text="# Title\n\nSWC-100\n\n## Description\nFoo weakness.\n")
    respx.get(
        "https://raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/entries/docs/SWC-101.md"
    ).respond(200, text="# Title\n\nSWC-101\n\n## Description\nBar weakness.\n")

    docs = await swc_mod.fetch(limit=2)
    assert len(docs) == 2
    assert docs[0].doc_type == "vulnerability_pattern"
    assert docs[0].metadata["swc_id"] == "SWC-100"


@respx.mock
async def test_swc_fetch_graceful_on_rate_limit():
    swc_mod = mod.get_source("swc_registry")
    assert swc_mod is not None
    # Tree API 403 → fall back to known IDs; raw fetches return non-200 → no docs.
    respx.get(
        "https://api.github.com/repos/SmartContractSecurity/SWC-registry/git/trees/master"
    ).respond(403, json={"message": "rate limit"})
    respx.get(
        "https://raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/entries/docs/SWC-100.md"
    ).respond(404)
    docs = await swc_mod.fetch(limit=1)
    assert docs == []


@respx.mock
async def test_defillama_fetch():
    dll = mod.get_source("defillama")
    assert dll is not None
    respx.get("https://api.llama.fi/v2/chains").respond(
        200, json=[{"name": "Ethereum", "tvl": 5e10, "change_24h": 0.5}]
    )
    docs = await dll.fetch(limit=1)
    assert len(docs) == 1
    assert docs[0].doc_type == "market_data"
