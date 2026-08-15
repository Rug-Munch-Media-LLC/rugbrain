"""Tests for rugbrain.ingest_sources — programmatic top-tier crypto sources."""

from __future__ import annotations

import respx

from rugbrain import ingest_sources as mod
from rugbrain.ingest_sources import (
    base,
    bridges,
    defihacklabs,
    defillama,
    eips,
    incidents,
    l2beat,
    mev,
    privacy,
    rekt,
    safe,
    swc,
    whitepapers,
    zk,
)

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
    "layerzero": "bridge_architecture",
    "axelar": "bridge_architecture",
    "ccip": "bridge_architecture",
    "l2beat": "l2_risk",
    "circom": "zk_circuit",
    "snarkjs": "zk_circuit",
    "mev_boost": "mev_architecture",
    "mev_boost_relay": "mev_architecture",
    "safe_contracts": "contract_code",
    "tornado_cash": "privacy_protocol",
    "guttedefi": "post_mortem",
    "immunefi_hack_analyses": "post_mortem",
    "blocksecteam_defi_poc": "exploit_poc",
    "slowmist_open_reports": "audit_finding",
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


# ── Proxy support (base client) ────────────────────────────────────────


def test_proxy_url_env(monkeypatch):
    monkeypatch.delenv("PROXY_URL", raising=False)
    assert base.proxy_url() is None
    monkeypatch.setenv("PROXY_URL", "http://user:pass@residential:8080")
    assert base.proxy_url() == "http://user:pass@residential:8080"
    monkeypatch.setenv("PROXY_URL", "   ")
    assert base.proxy_url() is None


# ── Bridge parsers (LayerZero / Axelar / CCIP) ─────────────────────────


def test_bridge_contract_to_doc():
    code = (
        "// SPDX-License-Identifier: MIT\n"
        "pragma solidity ^0.8.22;\n"
        "contract EndpointV2 { function send(uint32 dstEid) external {} }\n"
    )
    doc = bridges.bridge_contract_to_doc(
        protocol="LayerZero",
        source="layerzero",
        repo="LayerZero-Labs/LayerZero-v2",
        ref="main",
        rel_path="packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol",
        code=code,
    )
    assert doc is not None
    assert doc.doc_type == "bridge_architecture"
    assert doc.source == "layerzero"
    assert doc.metadata["protocol"] == "LayerZero"
    assert doc.metadata["contract_name"] == "EndpointV2"
    assert doc.metadata["archived"] is False
    assert doc.metadata["ecosystem"] == "Cross-Chain"
    assert "```solidity" in doc.content
    assert "0.8.22" in doc.metadata["pragma"]


def test_bridge_contract_to_doc_archived():
    doc = bridges.bridge_contract_to_doc(
        protocol="Chainlink CCIP",
        source="ccip",
        repo="smartcontractkit/ccip",
        ref="ccip-develop",
        rel_path="contracts/src/v0.8/ccip/Router.sol",
        code="pragma solidity ^0.8.0;\ncontract Router {}\n",
        archived=True,
    )
    assert doc is not None
    assert doc.metadata["archived"] is True
    assert "archived" in doc.content


def test_bridge_contract_empty_rejected():
    assert (
        bridges.bridge_contract_to_doc(
            protocol="X",
            source="x",
            repo="r/r",
            ref="main",
            rel_path="a.sol",
            code="   ",
        )
        is None
    )


# ── L2BEAT risk framework parsers ──────────────────────────────────────

L2BEAT_TS = """\
import { ProjectId } from '@l2beat/shared-pure'
import { Layer2 } from '../../types'

export const arbitrum: Layer2 = {
  id: ProjectId('arbitrum'),
  display: {
    name: 'Arbitrum One',
    slug: 'arbitrum',
    description: 'Arbitrum is an optimistic rollup that uses fraud proofs.',
    links: { websites: ['https://arbitrum.io'] },
  },
  config: { escrows: [] },
  riskView: {
    stateValidation: {
      value: 'Fraud proofs',
      description: 'Fraud proofs allow anyone to challenge a state root.',
      sentiment: 'bad',
    },
    dataAvailability: {
      value: 'Rollup',
      layers: ['L1'],
      description: 'All transaction data is posted to L1.',
      sentiment: 'good',
    },
    exitWindow: {
      value: '7 days',
      description: 'Users can exit within a 7-day window.',
      sentiment: 'bad',
    },
  },
}
"""


def test_l2beat_extract_risk_areas():
    areas = l2beat.extract_risk_areas(L2BEAT_TS)
    assert len(areas) == 3
    names = {n for n, _ in areas}
    assert names == {"stateValidation", "dataAvailability", "exitWindow"}
    assert any("challenge a state root" in d for _, d in areas)


def test_l2beat_project_to_doc():
    doc = l2beat.l2beat_project_to_doc(
        "packages/config/src/projects/arbitrum/arbitrum.ts", L2BEAT_TS
    )
    assert doc is not None
    assert doc.doc_type == "l2_risk"
    assert doc.source == "l2beat"
    assert doc.metadata["project"] == "arbitrum"
    assert "stateValidation" in doc.metadata["risk_areas"]
    assert doc.metadata["has_risk_view"] is True
    assert "Fraud proofs allow anyone to challenge" in doc.content
    assert "Arbitrum is an optimistic rollup" in doc.content


def test_l2beat_project_empty_rejected():
    assert l2beat.l2beat_project_to_doc("projects/x/x.ts", "  ") is None


# ── ZK toolchain parsers (Circom / SnarkJS) ────────────────────────────


def test_zk_doc_to_doc():
    md = "# Circom Language\n\n## Basic Operators\n\nCircom supports +, -, *, %.\n"
    doc = zk.zk_doc_to_doc(
        source="circom",
        protocol="Circom",
        repo="iden3/circom",
        ref="master",
        rel_path="mkdocs/docs/circom-language/basic-operators.md",
        markdown=md,
    )
    assert doc is not None
    assert doc.doc_type == "zk_circuit"
    assert doc.title == "Circom Language"
    assert doc.metadata["protocol"] == "Circom"
    assert doc.metadata["ecosystem"] == "ZK"
    assert "Basic Operators" in doc.content


def test_zk_doc_empty_rejected():
    assert (
        zk.zk_doc_to_doc(
            source="snarkjs",
            protocol="SnarkJS",
            repo="iden3/snarkjs",
            ref="master",
            rel_path="doc/x.md",
            markdown="",
        )
        is None
    )


# ── MEV architecture parsers (mev-boost / relay) ───────────────────────


def test_mev_doc_to_doc():
    md = "# MEV-Boost Specification\n\nThis document specifies the mev-boost API.\n"
    doc = mev.mev_doc_to_doc(
        source="mev_boost",
        protocol="MEV-Boost",
        repo="flashbots/mev-boost",
        ref="develop",
        rel_path="docs/specification.md",
        markdown=md,
    )
    assert doc is not None
    assert doc.doc_type == "mev_architecture"
    assert doc.title == "MEV-Boost Specification"
    assert doc.metadata["protocol"] == "MEV-Boost"
    assert doc.metadata["ecosystem"] == "MEV"


# ── Safe smart-account parser ──────────────────────────────────────────


def test_safe_parse_file():
    code = (
        "// SPDX-License-Identifier: LGPL-3.0-only\n"
        "pragma solidity >=0.7.0 <0.9.0;\n"
        "contract Safe { function setup(address[] calldata owners, uint256 threshold) external {} }\n"
    )
    doc = safe.parse_safe_file("contracts/Safe.sol", code)
    assert doc is not None
    assert doc.doc_type == "contract_code"
    assert doc.source == "safe_contracts"
    assert doc.metadata["contract_name"] == "Safe"
    assert doc.metadata["protocol"] == "Safe"
    assert "threshold" in doc.content


# ── Privacy protocol parser (Tornado Cash) ─────────────────────────────


def test_tornado_parse_file():
    code = "pragma solidity ^0.6.12;\ncontract Tornado { function deposit(bytes32 commitment) external payable {} }\n"
    doc = privacy.parse_tornado_file("contracts/Tornado.sol", code)
    assert doc is not None
    assert doc.doc_type == "privacy_protocol"
    assert doc.source == "tornado_cash"
    assert doc.metadata["archived"] is True
    assert doc.metadata["protocol"] == "Tornado Cash"
    assert doc.metadata["ecosystem"] == "Privacy"


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


@respx.mock
async def test_layerzero_fetch_end_to_end():
    lz = mod.get_source("layerzero")
    assert lz is not None
    respx.get("https://api.github.com/repos/LayerZero-Labs/LayerZero-v2/git/trees/main").respond(
        200,
        json=_tree_payload(
            "packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol",
            "packages/layerzero-v2/evm/oapp/contracts/oapp/OApp.sol",
        ),
    )
    respx.get(
        "https://raw.githubusercontent.com/LayerZero-Labs/LayerZero-v2/main/packages/layerzero-v2/evm/protocol/contracts/EndpointV2.sol"
    ).respond(200, text="pragma solidity ^0.8.22;\ncontract EndpointV2 {}\n")
    respx.get(
        "https://raw.githubusercontent.com/LayerZero-Labs/LayerZero-v2/main/packages/layerzero-v2/evm/oapp/contracts/oapp/OApp.sol"
    ).respond(200, text="pragma solidity ^0.8.22;\ncontract OApp {}\n")

    docs = await lz.fetch(limit=2)
    assert len(docs) == 2
    assert all(d.doc_type == "bridge_architecture" for d in docs)
    assert docs[0].metadata["protocol"] == "LayerZero"
    assert docs[0].metadata["contract_name"] == "EndpointV2"


@respx.mock
async def test_l2beat_fetch_end_to_end():
    lb = mod.get_source("l2beat")
    assert lb is not None
    respx.get("https://api.github.com/repos/l2beat/l2beat/git/trees/main").respond(
        200,
        json=_tree_payload(
            "packages/config/src/projects/arbitrum/arbitrum.ts",
            "packages/config/src/projects/arbitrum/arbitrum.test.ts",
        ),
    )
    respx.get(
        "https://raw.githubusercontent.com/l2beat/l2beat/main/packages/config/src/projects/arbitrum/arbitrum.ts"
    ).respond(200, text=L2BEAT_TS)

    docs = await lb.fetch(limit=5)
    assert len(docs) == 1  # .test.ts is skipped
    assert docs[0].doc_type == "l2_risk"
    assert docs[0].metadata["project"] == "arbitrum"
    assert "stateValidation" in docs[0].metadata["risk_areas"]


@respx.mock
async def test_tornado_fetch_graceful_on_rate_limit():
    tc = mod.get_source("tornado_cash")
    assert tc is not None
    respx.get("https://api.github.com/repos/tornadocash/tornado-core/git/trees/master").respond(
        403, json={"message": "rate limit"}
    )
    docs = await tc.fetch(limit=1)
    assert docs == []


# ── Incident / post-mortem sources (incidents.py) ────────────────────────


def test_parse_guttedefi_table():
    md = """
# All the Gutted DeFi Projects

| Date | Targeted Protocol | Exploited/Lost | Post-Mortem | Analysis | Audited? |
| ---- | ----------------- | -------------- | ----------- | -------- | -------- |
| ——   | **[Aug 2021]**    | ——             | ——          | ——       |         |
| 30th | Cream Finance     | $18.8 mln      | [Official](https://medium.com/cream) | [Halborn](https://halborn.com/cream) | No |
| 12th | DAO Maker         | 2261 ETH ($7 mln) | [Official](https://medium.com/daomaker) | | No |
|      | TOTAL LOSS       | ~ $720 mln     | | | |
"""
    rows = incidents.parse_guttedefi_table(md)
    assert len(rows) == 2
    assert rows[0].protocol == "Cream Finance"
    assert rows[0].month_year == "Aug 2021"
    assert rows[0].day == "30th"
    assert rows[0].loss_usd == 18_800_000
    assert rows[0].post_mortem_links == ("https://medium.com/cream",)
    assert rows[1].protocol == "DAO Maker"


def test_guttedefi_to_doc():
    inc = incidents.GutteDeFiIncident(
        day="30th",
        month_year="Aug 2021",
        protocol="Cream Finance",
        loss_raw="$18.8 mln",
        loss_usd=18_800_000,
        post_mortem_links=("https://medium.com/cream",),
        analysis_links=(),
        analysis_text="Flash Loan",
        audited="No",
        row_index=0,
    )
    doc = incidents.guttedefi_to_doc(inc)
    assert doc.doc_type == "post_mortem"
    assert doc.metadata["protocol"] == "Cream Finance"
    assert doc.metadata["date"] == "30th Aug 2021"
    assert doc.metadata["loss_usd"] == 18_800_000
    assert doc.metadata["severity"] == "critical"
    assert doc.metadata["vulnerability_class"] == "flash-loan"
    assert doc.url == "https://medium.com/cream"


def test_parse_immunefi_hack_analyses():
    md = """
# Hack Analyses

## [Hack Analysis: BonqDAO, February 2023](https://medium.com/immunefi/bonq)

BonqDAO protocol was hacked resulting in a loss of approximately $120 million
due to a price oracle manipulation attack.

 - Vulnerability type: Price oracle manipulation

## [Hack Analysis: Saddle Finance, April 2022](https://medium.com/immunefi/saddle)

Saddle Finance ... price miscalculation when swapping a token for an LP token.

- Vulnerability type: Logic, price calculation.
"""
    entries = incidents.parse_immunefi_hack_analyses(md)
    assert len(entries) == 2
    assert entries[0].title == "BonqDAO"
    assert entries[0].month_year == "February 2023"
    assert entries[0].url == "https://medium.com/immunefi/bonq"
    assert "oracle" in entries[0].vulnerability_type.lower()
    assert entries[1].vulnerability_type.startswith("Logic")


def test_immunefi_to_doc():
    a = incidents.ImmunefiHackAnalysis(
        title="BonqDAO",
        month_year="February 2023",
        url="https://medium.com/immunefi/bonq",
        description="Price oracle manipulation caused $120m loss.",
        vulnerability_type="Price oracle manipulation",
    )
    doc = incidents.immunefi_to_doc(a)
    assert doc.doc_type == "post_mortem"
    assert doc.metadata["protocol"] == "BonqDAO"
    assert doc.metadata["vulnerability_class"] == "oracle-manipulation"
    assert doc.metadata["vulnerability_type"] == "Price oracle manipulation"
    assert "Root cause" in doc.content


def test_blocksecteam_poc_to_doc():
    doc = incidents.blocksecteam_poc_to_doc(
        "CREAM/README.md",
        "# CREAM PoC\n\nFlash loan exploit using DEX to repay the flash swap.\n",
    )
    assert doc is not None
    assert doc.doc_type == "exploit_poc"
    assert doc.metadata["protocol"] == "CREAM"
    assert "flash-loan" in doc.metadata["vulnerability_class"]
    assert "https://github.com/blocksecteam/defi_poc/tree/main/CREAM" in doc.url
    assert incidents.blocksecteam_poc_to_doc("README.md", "x") is None


def test_slowmist_report_to_doc():
    md = """# IoTeX-voting-contracts Smart Contract Security Audit Report

## Summary

* Audit Date: February 28, 2019
* Audit Result: Passed
* Audit Team: SlowMist Security Team

## Report

Details...
"""
    doc = incidents.slowmist_report_to_doc("open-report/IoTeX.md", md)
    assert doc is not None
    assert doc.doc_type == "audit_finding"
    assert doc.metadata["audit_result"] == "Passed"
    assert doc.metadata["audit_date"] == "February 28, 2019"
    assert "IoTeX" in doc.title
    assert incidents.slowmist_report_to_doc("open-report/empty.md", "   ") is None
