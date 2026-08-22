"""Tests for rugbrain.ingest_sources — programmatic top-tier crypto sources."""

from __future__ import annotations

import importlib as _il
from typing import Any

import pytest
import respx

ingest_sources = _il.import_module("rugbrain.ingest_sources")

mod = ingest_sources
from rugbrain.ingest_sources import (
    base,
    blacklists,
    blocksecteam_phishing,
    bridges,
    chainabuse,
    cisa_kev,
    coinspect_evm_attacks,
    crypto_rekts,
    defihacklabs,
    defillama,
    deflabx,
    eips,
    emperor_cftc,
    feeds,
    hf_audit_archive,
    incidents,
    l2beat,
    mev,
    openphish,
    opensanctions,
    ossf_malicious,
    phishdestroy,
    privacy,
    rekt,
    safe,
    samczsun,
    seal911,
    swc,
    trailofbits_publications,
    urlhaus,
    whitepapers,
    yaudit_fork_bugs,
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
    "chainabuse": "crime_report",
    "crypto_rekts": "post_mortem",
    # ── 2026-08 new sources (rag-sources-2026-08.json, top-20 wiring) ──────
    "phishdestroy_destroylist": "scam_entity",
    "deflabx_solrpds": "scam_entity",
    "seal_911": "incident",
    "trailofbits_publications": "audit_finding",
    "gcf3711_audit_report_archive_hf": "audit_finding",
    "emperor_mew_cftc_actions": "enforcement_action",
    "opensanctions": "sanction_entity",
    "samczsun_blog": "research",
    "blocksecteam_phishing_contracts": "scam_entity",
    "ossf_malicious_packages": "vulnerability_pattern",
    "trailofbits_blog_rss": "research",
    "zellic_blog_rss": "research",
    "immunefi_blog_rss": "research",
    "neodyme_blog_rss": "research",
    "cftc_press_releases_rss": "enforcement_action",
    "cisa_kev": "vulnerability_pattern",
    "openphish_feed": "scam_entity",
    "urlhaus_feed": "scam_entity",
    "yaudit_defi_fork_bugs": "post_mortem",
    "coinspect_learn_evm_attacks": "exploit_poc",
}


def test_registry_contains_all_required_sources() -> None:
    names = mod.list_sources()
    assert set(REQUIRED) <= set(names), f"missing: {set(REQUIRED) - set(names)}"
    assert len(names) == len(set(names))  # no duplicates


def test_doc_types_assigned() -> None:
    for name, doc_type in REQUIRED.items():
        src = mod.get_source(name)
        assert src is not None
        assert src.doc_type == doc_type, f"{name}: expected {doc_type}, got {src.doc_type}"


def test_get_source_unknown() -> None:
    assert mod.get_source("does_not_exist") is None


def test_ingest_doc_shape() -> None:
    doc = base.IngestDoc(content="x", doc_id="a:1", metadata={"k": "v"}, doc_type="t", source="s")
    assert doc.content == "x"
    assert doc.doc_id == "a:1"
    assert doc.metadata == {"k": "v"}
    assert doc.doc_type == "t"
    assert doc.source == "s"
    assert doc.title == ""
    assert doc.url == ""


def test_to_crawl_document_bridge() -> None:
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


def test_defihacklabs_parse_sol() -> None:
    code = "// Reentrancy attack PoC\n// steals all funds\ncontract Hack { function pwn() external {} }"
    doc = defihacklabs.parse_sol_file("src/test/2023-01/Foo_exp.sol", code)
    assert doc is not None
    assert doc.doc_type == "exploit_poc"
    assert doc.metadata["category"] == "reentrancy"
    assert doc.metadata["severity"] == "critical"
    assert doc.metadata["contract_name"] == "Hack"
    assert "Reentrancy attack PoC" in doc.content
    assert doc.source == "defihacklabs"


def test_defihacklabs_parse_sol_empty() -> None:
    assert defihacklabs.parse_sol_file("src/a.sol", "   ") is None


def test_defihacklabs_parse_incident_readme() -> None:
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


def test_swc_markdown_to_doc() -> None:
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


def test_swc_markdown_empty_rejected() -> None:
    assert swc.swc_markdown_to_doc("SWC-999", "# Title\n\nnothing here") is None


# ── EIPs / BIPs parsers ─────────────────────────────────────────────────


def test_eips_parse_frontmatter() -> None:
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


def test_standard_to_doc() -> None:
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


def test_rekt_parse_leaderboard() -> None:
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


def test_rekt_article_to_doc() -> None:
    doc = rekt.article_to_doc(rekt.RektArticle(rank=1, title="Wormhole", url="/wormhole-rekt/"))
    assert doc.doc_type == "post_mortem"
    assert doc.url == "https://rekt.news/wormhole-rekt/"
    assert doc.metadata["rank"] == 1


# ── Whitepapers parsers ─────────────────────────────────────────────────


def test_whitepapers_markdown_to_doc() -> None:
    md = "# Bitcoin: A Peer-to-Peer Electronic Cash System\n\nAbstract...\n"
    doc = whitepapers.markdown_to_doc("bitcoin.md", md)
    assert doc is not None
    assert doc.doc_type == "whitepaper"
    assert doc.title == "Bitcoin: A Peer-to-Peer Electronic Cash System"
    assert doc.metadata["format"] == "markdown"


def test_whitepapers_pdf_stub() -> None:
    doc = whitepapers.pdf_to_stub("ethereum.pdf", size=500_000)
    assert doc.doc_type == "whitepaper"
    assert doc.metadata["format"] == "pdf"
    assert doc.metadata["pending_extraction"] is True


# ── DeFiLlama parsers ───────────────────────────────────────────────────


def test_defillama_tvl_to_docs() -> None:
    chains = [
        {"name": "Ethereum", "tvl": 50000000000.0, "change_24h": 1.5},
        {"name": "Solana", "tvl": 0},
    ]
    docs = defillama.tvl_to_docs(chains)
    assert len(docs) == 2
    assert docs[0].doc_type == "market_data"
    assert docs[0].metadata["chain"] == "ethereum"
    assert docs[0].metadata["tvl_usd"] == 50000000000.0


def test_defillama_protocol_to_doc() -> None:
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


def test_defillama_hacks_to_docs() -> None:
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


def test_proxy_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROXY_URL", raising=False)
    assert base.proxy_url() is None
    monkeypatch.setenv("PROXY_URL", "http://user:pass@residential:8080")
    assert base.proxy_url() == "http://user:pass@residential:8080"
    monkeypatch.setenv("PROXY_URL", "   ")
    assert base.proxy_url() is None


# ── Bridge parsers (LayerZero / Axelar / CCIP) ─────────────────────────


def test_bridge_contract_to_doc() -> None:
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


def test_bridge_contract_to_doc_archived() -> None:
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


def test_bridge_contract_empty_rejected() -> None:
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


def test_l2beat_extract_risk_areas() -> None:
    areas = l2beat.extract_risk_areas(L2BEAT_TS)
    assert len(areas) == 3
    names = {n for n, _ in areas}
    assert names == {"stateValidation", "dataAvailability", "exitWindow"}
    assert any("challenge a state root" in d for _, d in areas)


def test_l2beat_project_to_doc() -> None:
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


def test_l2beat_project_empty_rejected() -> None:
    assert l2beat.l2beat_project_to_doc("projects/x/x.ts", "  ") is None


# ── ZK toolchain parsers (Circom / SnarkJS) ────────────────────────────


def test_zk_doc_to_doc() -> None:
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


def test_zk_doc_empty_rejected() -> None:
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


def test_mev_doc_to_doc() -> None:
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


def test_safe_parse_file() -> None:
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


def test_tornado_parse_file() -> None:
    code = "pragma solidity ^0.6.12;\ncontract Tornado { function deposit(bytes32 commitment) external payable {} }\n"
    doc = privacy.parse_tornado_file("contracts/Tornado.sol", code)
    assert doc is not None
    assert doc.doc_type == "privacy_protocol"
    assert doc.source == "tornado_cash"
    assert doc.metadata["archived"] is True
    assert doc.metadata["protocol"] == "Tornado Cash"
    assert doc.metadata["ecosystem"] == "Privacy"


# ── Fetch paths (mocked network) ────────────────────────────────────────


def _tree_payload(*paths: str) -> dict[str, Any]:
    return {"tree": [{"path": p, "type": "blob", "size": 100} for p in paths]}


@respx.mock
async def test_swc_fetch_end_to_end() -> None:
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
async def test_swc_fetch_graceful_on_rate_limit() -> None:
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
async def test_defillama_fetch() -> None:
    dll = mod.get_source("defillama")
    assert dll is not None
    respx.get("https://api.llama.fi/v2/chains").respond(
        200, json=[{"name": "Ethereum", "tvl": 5e10, "change_24h": 0.5}]
    )
    docs = await dll.fetch(limit=1)
    assert len(docs) == 1
    assert docs[0].doc_type == "market_data"


@respx.mock
async def test_layerzero_fetch_end_to_end() -> None:
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
async def test_l2beat_fetch_end_to_end() -> None:
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
async def test_tornado_fetch_graceful_on_rate_limit() -> None:
    tc = mod.get_source("tornado_cash")
    assert tc is not None
    respx.get("https://api.github.com/repos/tornadocash/tornado-core/git/trees/master").respond(
        403, json={"message": "rate limit"}
    )
    docs = await tc.fetch(limit=1)
    assert docs == []


# ── Incident / post-mortem sources (incidents.py) ────────────────────────


def test_parse_guttedefi_table() -> None:
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


def test_guttedefi_to_doc() -> None:
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


def test_parse_immunefi_hack_analyses() -> None:
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


def test_immunefi_to_doc() -> None:
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


def test_blocksecteam_poc_to_doc() -> None:
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


def test_slowmist_report_to_doc() -> None:
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


# ── Chainabuse scam-report source (chainabuse.py) ───────────────────────


def test_chainabuse_report_to_doc() -> None:
    report = {
        "id": 12345,
        "scam_type": {"id": 14, "type": "Phishing"},
        "description": "Fake exchange site stealing deposits.",
        "reported_address": "0xAbC1234567890AbC1234567890AbC1234567890",
        "scam_url": "https://fake-exchange.example",
        "chain": "ethereum",
        "date": "2026-08-01",
    }
    doc = chainabuse.report_to_doc(report)
    assert doc is not None
    assert doc.doc_type == "crime_report"
    assert doc.metadata["collection"] == "crime_reports"
    assert doc.metadata["category"] == "Phishing"
    assert doc.metadata["severity"] == "critical"
    assert doc.metadata["address"] == "0xAbC1234567890AbC1234567890AbC1234567890"
    assert doc.metadata["entity_type"] == "address"
    assert "Fake exchange site" in doc.content
    assert doc.doc_id.startswith("chainabuse:12345:")


def test_chainabuse_report_to_doc_string_scam_type_and_domain() -> None:
    report = {
        "id": 99,
        "category": "Investment Scam",
        "domain": "bad-platform.example",
        "description": "Fake yield platform.",
    }
    doc = chainabuse.report_to_doc(report)
    assert doc is not None
    assert doc.metadata["entity_type"] == "domain"
    assert doc.metadata["entity"] == "bad-platform.example"
    assert doc.metadata["category"] == "Investment Scam"
    assert doc.metadata["severity"] == "critical"
    assert doc.metadata["collection"] == "crime_reports"


def test_chainabuse_report_empty_rejected() -> None:
    assert chainabuse.report_to_doc({}) is None
    assert chainabuse.report_to_doc({"id": 1}) is None  # no entity, no description


def test_chainabuse_extract_reports_envelopes() -> None:
    bare = [{"id": 1}, {"id": 2}]
    assert len(chainabuse._extract_reports(bare)) == 2
    assert len(chainabuse._extract_reports({"reports": bare})) == 2
    assert len(chainabuse._extract_reports({"items": bare})) == 2
    assert chainabuse._extract_reports({"count": 0}) == []
    assert chainabuse._extract_reports("junk") == []


@respx.mock
async def test_chainabuse_fetch_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAINABUSE_API_KEY", "test-key")
    monkeypatch.setattr(chainabuse, "BASE_URL", "https://api.chainabuse.com")
    src = mod.get_source("chainabuse")
    assert src is not None
    respx.get("https://api.chainabuse.com/v0/reports").respond(
        200,
        json={
            "reports": [
                {
                    "id": 1,
                    "scam_type": {"id": 14, "type": "Phishing"},
                    "description": "Fake airdrop page.",
                    "reported_address": "0x" + "1" * 40,
                    "scam_url": "https://airdrop.example",
                    "date": "2026-08-01",
                },
                {
                    "id": 2,
                    "category": "Wallet Drainer",
                    "description": "Approval drainer dapp.",
                    "reported_address": "0x" + "2" * 40,
                    "date": "2026-08-02",
                },
            ]
        },
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "crime_report" for d in docs)
    assert all(d.metadata["collection"] == "crime_reports" for d in docs)
    assert docs[0].metadata["severity"] == "critical"
    # Id stability: re-fetching yields identical doc_ids (idempotent seeds).
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


@respx.mock
async def test_chainabuse_fetch_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHAINABUSE_API_KEY", raising=False)
    src = mod.get_source("chainabuse")
    assert src is not None
    docs = await src.fetch(limit=5)
    assert docs == []


@respx.mock
async def test_chainabuse_fetch_graceful_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHAINABUSE_API_KEY", "test-key")
    monkeypatch.setattr(chainabuse, "BASE_URL", "https://api.chainabuse.com")
    src = mod.get_source("chainabuse")
    assert src is not None
    respx.get("https://api.chainabuse.com/v0/reports").respond(500)
    docs = await src.fetch(limit=5)
    assert docs == []


# ── crypto-rekts incident knowledgebase (crypto_rekts.py) ───────────────


def test_crypto_rekt_to_doc() -> None:
    item = {
        "id": 3058,
        "title": "Beanstalk",
        "project_name": "Beanstalk",
        "date": "2022-4-18",
        "funds_lost": 181000000,
        "funds_returned": 0,
        "scam_type": {"id": 15, "type": "Flash Loan Attack"},
        "description": "<p><strong>Quick Summary</strong></p><p>A flash loan "
        "attack occurred on Beanstalk Governance resulting in a theft of $77M.</p>",
        "scamNetworks": [
            {"networks": {"id": 1003, "name": "Ethereum"}},
        ],
        "proof_link": "https://rekt.news/beanstalk-rekt/",
        "auditedBy": [{"partner": {"name": "CertiK"}}],
    }
    doc = crypto_rekts.crypto_rekt_to_doc(item)
    assert doc is not None
    assert doc.doc_type == "post_mortem"
    assert doc.metadata["collection"] == "defi_exploits"
    assert doc.metadata["protocol"] == "Beanstalk"
    assert doc.metadata["date"] == "2022-4-18"
    assert doc.metadata["loss_usd"] == 181_000_000
    assert doc.metadata["category"] == "Flash Loan Attack"
    assert doc.metadata["vulnerability_class"] == "flash-loan"
    assert doc.metadata["severity"] == "critical"
    assert doc.metadata["chain"] == "Ethereum"
    assert doc.metadata["audited_by"] == ["CertiK"]
    assert doc.url == "https://rekt.news/beanstalk-rekt/"
    assert "A flash loan attack occurred" in doc.content
    assert doc.doc_id.startswith("crypto_rekts:3058:")


def test_crypto_rekt_to_doc_governance_keyword_detection() -> None:
    item = {
        "id": 42,
        "title": "AnubisDAO",
        "date": "2021-10-28",
        "funds_lost": 5860000,
        "scam_type": {"id": 18, "type": "Other"},
        "description": "Governance proposal drained the treasury.",
    }
    doc = crypto_rekts.crypto_rekt_to_doc(item)
    assert doc is not None
    assert doc.metadata["vulnerability_class"] == "governance-attack"
    assert doc.metadata["severity"] == "high"


def test_crypto_rekt_empty_rejected() -> None:
    assert crypto_rekts.crypto_rekt_to_doc({}) is None
    assert crypto_rekts.crypto_rekt_to_doc({"title": "X"}) is None  # no id


def test_parse_rektdb_tolerates_envelope_and_list() -> None:
    envelope = '{"items": [{"id": 1, "title": "A"}], "currentPage": 1, "lastPage": 1, "count": 1}'
    items = crypto_rekts.parse_rektdb(envelope)
    assert len(items) == 1
    assert items[0]["title"] == "A"
    assert crypto_rekts.parse_rektdb('[{"id": 2}]') == [{"id": 2}]
    assert crypto_rekts.parse_rektdb("not json") == []
    assert crypto_rekts.parse_rektdb("") == []


@respx.mock
async def test_crypto_rekts_fetch_mocked() -> None:
    src = mod.get_source("crypto_rekts")
    assert src is not None
    payload = {
        "items": [
            {
                "id": 1,
                "title": "Beanstalk",
                "date": "2022-4-18",
                "funds_lost": 181000000,
                "scam_type": {"id": 15, "type": "Flash Loan Attack"},
                "description": "Governance flash loan exploit.",
            },
            {
                "id": 2,
                "title": "BonkDAO",
                "date": "2026-7-6",
                "funds_lost": 19300000,
                "scam_type": {"id": 18, "type": "Other"},
                "description": "Governance treasury transfer via bought voting power.",
            },
        ],
        "count": 2,
    }
    respx.get("https://raw.githubusercontent.com/liqtags/crypto-rekts/main/rektdb.json").respond(
        200, json=payload
    )

    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "post_mortem" for d in docs)
    assert all(d.metadata["collection"] == "defi_exploits" for d in docs)
    assert docs[1].metadata["protocol"] == "BonkDAO"
    assert docs[1].metadata["vulnerability_class"] == "governance-attack"
    # Id stability: deterministic doc_id seeds across re-fetches.
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


@respx.mock
async def test_crypto_rekts_fetch_limit() -> None:
    src = mod.get_source("crypto_rekts")
    assert src is not None
    payload = {
        "items": [
            {"id": i, "title": f"Incident {i}", "date": "2022-1-1", "funds_lost": 1000}
            for i in range(1, 21)
        ]
    }
    respx.get("https://raw.githubusercontent.com/liqtags/crypto-rekts/main/rektdb.json").respond(
        200, json=payload
    )
    docs = await src.fetch(limit=3)
    assert len(docs) == 3
    assert docs[0].doc_id.startswith("crypto_rekts:1:")


@respx.mock
async def test_crypto_rekts_fetch_graceful_on_error() -> None:
    src = mod.get_source("crypto_rekts")
    assert src is not None
    respx.get("https://raw.githubusercontent.com/liqtags/crypto-rekts/main/rektdb.json").respond(
        500
    )
    docs = await src.fetch(limit=5)
    assert docs == []


# ── Existing blacklist coverage (candidate #4: Phishing.Database + MetaMask) ─


def test_blacklists_has_phishing_database_and_metamask_feeds() -> None:
    feed_keys = {f.key for f in blacklists.FEEDS}
    assert "phishing_database" in feed_keys
    assert "metamask_blocklist" in feed_keys
    src = mod.get_source("blacklists")
    assert src is not None
    assert src.default_collection == "scam_intel_v2"


# ── 2026-08 top-20 sources (rag-sources-2026-08.json) ────────────────────


# ── phishdestroy/destroylist ─────────────────────────────────────────────


def test_phishdestroy_parse_list_json() -> None:
    domains = phishdestroy.parse_list_json('["0-105.com", "evil.example", " good.example "]')
    assert domains == ["0-105.com", "evil.example", "good.example"]
    # dict envelope + plain-lines fallback
    assert phishdestroy.parse_list_json('{"domains": ["a.com"]}') == ["a.com"]
    assert phishdestroy.parse_list_json("a.com\n# comment\nb.com\n") == ["a.com", "b.com"]
    # Plain text falls back to the line parser (fail-open), never raises.
    assert phishdestroy.parse_list_json("not json") == ["not json"]


@respx.mock
async def test_phishdestroy_fetch_mocked() -> None:
    src = mod.get_source("phishdestroy_destroylist")
    assert src is not None
    respx.get(phishdestroy.LIST_URL).respond(200, json=["evil-1.com", "evil-2.com"])
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "scam_entity" for d in docs)
    assert all(d.metadata["collection"] == "scam_intel_v2" for d in docs)
    assert docs[0].metadata["entity"] == "evil-1.com"
    assert docs[0].metadata["labels"] == ["phishing"]
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


@respx.mock
async def test_phishdestroy_fetch_graceful_on_error() -> None:
    src = mod.get_source("phishdestroy_destroylist")
    assert src is not None
    respx.get(phishdestroy.LIST_URL).respond(500)
    docs = await src.fetch(limit=5)
    assert docs == []


# ── DeFiLabX/SolRPDS ─────────────────────────────────────────────────────


def test_deflabx_wallet_to_doc() -> None:
    record = {
        "MINT": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "LIQUIDITY_POOL_ADDRESS": "821L241gacEqKJiCp9H7E9rqRXB31WoHLBqwhn8NMyWS",
        "TOTAL_ADDED_LIQUIDITY": "131.42666",
        "TOTAL_REMOVED_LIQUIDITY": "37.879524",
        "ADD_TO_REMOVE_RATIO": "3.469596397",
        "FIRST_POOL_ACTIVITY_TIMESTAMP": "2021-12-30 21:55:04.000",
        "INACTIVITY_STATUS": "Active",
    }
    doc = deflabx.wallet_to_doc(record, 2021)
    assert doc is not None
    assert doc.doc_type == "scam_entity"
    assert doc.metadata["collection"] == "defi_exploits"
    assert doc.metadata["chain"] == "Solana"
    assert doc.metadata["token_address"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # noqa: S105
    assert doc.metadata["labels"] == ["rugpull"]
    assert doc.doc_id == f"deflabx_solrpds:{record['MINT']}:2021"
    assert deflabx.wallet_to_doc({"LIQUIDITY_POOL_ADDRESS": "x"}, 2021) is None


@respx.mock
async def test_deflabx_fetch_mocked() -> None:
    src = mod.get_source("deflabx_solrpds")
    assert src is not None
    respx.get(f"{deflabx.RAW_BASE}/2021.json").respond(200, json=[{"MINT": "mint2021"}])
    respx.get(f"{deflabx.RAW_BASE}/2022.json").respond(200, json=[{"MINT": "mint2022"}])
    respx.get(f"{deflabx.RAW_BASE}/2023.json").respond(404)  # missing year fails open
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.metadata["collection"] == "defi_exploits" for d in docs)
    assert docs[0].doc_id == "deflabx_solrpds:mint2021:2021"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── security-alliance/seal-911 ───────────────────────────────────────────


def test_seal911_parse_activity_log() -> None:
    md = (
        "# Log\n\n"
        "- [RISEx incident resolution](https://x.com/risextrade/status/1)\n"
        "- [Across incident resolution](https://x.com/acrossprotocol/status/2)\n"
    )
    entries = seal911.parse_activity_log(md)
    assert len(entries) == 2
    assert entries[0].title == "RISEx incident resolution"
    assert entries[0].url == "https://x.com/risextrade/status/1"


def test_seal911_to_doc() -> None:
    doc = seal911.seal911_to_doc(
        seal911.Seal911Entry(
            "Across incident resolution", "https://x.com/acrossprotocol/status/2", 0
        )
    )
    assert doc.doc_type == "incident"
    assert doc.metadata["collection"] == "threat_intel_v2"
    assert doc.doc_id.startswith("seal_911:across-incident-resolution:")


@respx.mock
async def test_seal911_fetch_mocked() -> None:
    src = mod.get_source("seal_911")
    assert src is not None
    respx.get(seal911.ACTIVITY_URL).respond(
        200, text="- [Kelp incident resolution](https://x.com/KelpDAO/status/1)\n"
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 1
    assert docs[0].doc_type == "incident"
    assert docs[0].metadata["collection"] == "threat_intel_v2"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── trailofbits/publications ─────────────────────────────────────────────


def test_tob_csv_row_to_doc() -> None:
    headers = ["Project", "Issue", "Type", "CS Severity", "ToB Severity", "Difficulty"]
    doc = trailofbits_publications.csv_row_to_doc(
        "datasets/smart_contract_audit_findings/other_audit_sources/chainsecurity.csv",
        headers,
        ["Blockimmo", "logic bug", "logic", "med", "medium", "medium"],
        0,
    )
    assert doc.doc_type == "audit_finding"
    assert doc.metadata["collection"] == "contract_audits"
    assert doc.metadata["protocol"] == "Blockimmo"
    assert doc.metadata["severity"] == "medium"
    assert doc.doc_id.startswith("trailofbits_publications:")


def test_tob_markdown_review_to_doc() -> None:
    doc = trailofbits_publications.markdown_review_to_doc(
        "reviews/celo_bugfixes.md", "# Celo bug fixes\n\nDetails about reentrancy fix.\n"
    )
    assert doc is not None
    assert doc.metadata["collection"] == "contract_audits"
    assert "Celo" in doc.title
    assert trailofbits_publications.markdown_review_to_doc("reviews/x.md", "   ") is None


@respx.mock
async def test_tob_publications_fetch_mocked() -> None:
    src = mod.get_source("trailofbits_publications")
    assert src is not None
    tree_url = "https://api.github.com/repos/trailofbits/publications/git/trees/master"
    respx.get(f"{tree_url}?recursive=1").respond(
        200,
        json={
            "tree": [
                {
                    "path": "datasets/smart_contract_audit_findings/other_audit_sources/chainsecurity.csv",
                    "type": "blob",
                    "size": 100,
                },
                {"path": "reviews/celo_bugfixes.md", "type": "blob", "size": 100},
            ]
        },
    )
    respx.get(
        "https://raw.githubusercontent.com/trailofbits/publications/master/datasets/smart_contract_audit_findings/other_audit_sources/chainsecurity.csv"
    ).respond(200, text="Project,Issue,Type,CS Severity\nBlockimmo,logic bug,logic,med\n")
    respx.get(
        "https://raw.githubusercontent.com/trailofbits/publications/master/reviews/celo_bugfixes.md"
    ).respond(200, text="# Celo bug fixes\n\nReentrancy details.\n")
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.metadata["collection"] == "contract_audits" for d in docs)
    assert all(d.doc_type == "audit_finding" for d in docs)
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── gcf3711/audit-report-archive (HF) ────────────────────────────────────


def test_gcf3711_row_to_doc() -> None:
    row = {
        "pdf": {"src": "https://datasets-server.huggingface.co/files/x.pdf", "size_bytes": 2048},
        "label": "hexens",
    }
    doc = hf_audit_archive.row_to_doc(row, 0)
    assert doc is not None
    assert doc.doc_type == "audit_finding"
    assert doc.metadata["collection"] == "contract_audits"
    assert doc.metadata["audit_team"] == "hexens"
    assert doc.metadata["pdf_src"].endswith("x.pdf")
    assert hf_audit_archive.row_to_doc({}, 0) is None


@respx.mock
async def test_gcf3711_fetch_mocked() -> None:
    src = mod.get_source("gcf3711_audit_report_archive_hf")
    assert src is not None
    url = f"{hf_audit_archive.ROWS_URL}10"
    respx.get(url).respond(
        200,
        json={
            "rows": [
                {"row": {"pdf": {"src": "https://x/a.pdf", "size_bytes": 1}, "label": "cantina"}},
                {"row": {"pdf": {"src": "https://x/b.pdf", "size_bytes": 2}, "label": None}},
            ]
        },
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.metadata["collection"] == "contract_audits" for d in docs)
    assert all(d.doc_type == "audit_finding" for d in docs)
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── emperor-mew/cftc-actions ─────────────────────────────────────────────


def test_emperor_cftc_release_to_doc() -> None:
    item = {
        "release_number": "9131-26",
        "title": "CFTC Staff Issues No-Action Letter",
        "release_date": "2026-05-13",
        "url": "https://www.cftc.gov/PressRoom/PressReleases/9131-26",
        "is_enforcement": 1,
        "enforcement_type": "other",
        "type_label": "Other enforcement",
    }
    doc = emperor_cftc.release_to_doc(item)
    assert doc is not None
    assert doc.doc_type == "enforcement_action"
    assert doc.metadata["collection"] == "regulation_enforcement"
    assert doc.metadata["agency"] == "CFTC"
    assert doc.metadata["is_enforcement"] is True
    assert doc.metadata["severity"] == "critical"
    assert doc.doc_id == "emperor_mew_cftc_actions:9131-26"
    assert emperor_cftc.release_to_doc({}) is None


@respx.mock
async def test_emperor_cftc_fetch_mocked() -> None:
    src = mod.get_source("emperor_mew_cftc_actions")
    assert src is not None
    respx.get(emperor_cftc.API_URL).respond(
        200,
        json={
            "items": [
                {
                    "release_number": "9131-26",
                    "title": "No-Action Letter",
                    "release_date": "2026-05-13",
                    "url": "https://www.cftc.gov/x",
                    "is_enforcement": 0,
                    "enforcement_type": "",
                    "type_label": "",
                },
                {
                    "release_number": "9120-26",
                    "title": "Crypto Fraud Enforcement",
                    "release_date": "2026-05-01",
                    "url": "https://www.cftc.gov/y",
                    "is_enforcement": 1,
                    "enforcement_type": "crypto_fraud",
                    "type_label": "Crypto fraud",
                },
            ]
        },
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.metadata["collection"] == "regulation_enforcement" for d in docs)
    assert docs[1].metadata["enforcement_type"] == "crypto_fraud"
    assert docs[1].metadata["severity"] == "critical"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── OpenSanctions bulk ───────────────────────────────────────────────────


def test_opensanctions_wallet_to_doc() -> None:
    entity = {
        "id": "NK-abc123",
        "caption": "OFAC sanctioned wallet",
        "schema": "CryptoWallet",
        "datasets": ["us_ofac"],
        "target": True,
        "properties": {
            "address": ["0x1234567890123456789012345678901234567890"],
            "asset": ["USDT"],
            "blockchain": ["ethereum"],
        },
    }
    doc = opensanctions.wallet_entity_to_doc(entity)
    assert doc is not None
    assert doc.doc_type == "sanction_entity"
    assert doc.metadata["collection"] == "regulation_enforcement"
    assert doc.metadata["address"] == "0x1234567890123456789012345678901234567890"
    assert doc.metadata["asset"] == "USDT"
    assert doc.metadata["labels"] == ["sanctioned"]
    assert doc.doc_id == "opensanctions:NK-abc123"
    assert opensanctions.wallet_entity_to_doc({"schema": "Person"}) is None


@respx.mock
async def test_opensanctions_fetch_mocked() -> None:
    src = mod.get_source("opensanctions")
    assert src is not None
    ftm = (
        '{"id":"NK-1","caption":"wallet a","schema":"CryptoWallet","datasets":["us_ofac"],"target":true,"properties":{"address":["0x1111111111111111111111111111111111111111"]}}\n'
        '{"id":"P-1","caption":"some person","schema":"Person","datasets":["us_ofac"]}\n'
        '{"id":"NK-2","caption":"wallet b","schema":"CryptoWallet","datasets":["gb_sanctions"],"target":false,"properties":{"address":["0x2222222222222222222222222222222222222222"],"asset":["ETH"],"blockchain":["ethereum"]}}\n'
    )
    respx.get(opensanctions.ENTITIES_URL).respond(200, text=ftm)
    docs = await src.fetch(limit=10)
    assert len(docs) == 2  # Person entity skipped
    assert all(d.doc_type == "sanction_entity" for d in docs)
    assert all(d.metadata["collection"] == "regulation_enforcement" for d in docs)
    assert docs[0].metadata["labels"] == ["sanctioned"]
    assert docs[1].metadata["asset"] == "ETH"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── samczsun.com ─────────────────────────────────────────────────────────


def test_samczsun_extract_article_links() -> None:
    html = '<a href="/escaping-the-dark-forest/">a</a><a href="/#about">x</a><a href="/icon.png">i</a><a href="/hiding-in-plain-sight/">b</a>'
    links = samczsun.extract_article_links(html)
    assert links == ["escaping-the-dark-forest", "hiding-in-plain-sight"]


def test_samczsun_extract_article_text() -> None:
    html = "<html><head><title>Escaping the Dark Forest - samczsun</title></head><body><nav>menu</nav><article><h1>Escaping</h1><p>Research body text.</p></article></body></html>"
    title, body = samczsun.extract_article_text(html)
    assert title == "Escaping the Dark Forest"
    assert "Research body text." in body


@respx.mock
async def test_samczsun_fetch_mocked() -> None:
    src = mod.get_source("samczsun_blog")
    assert src is not None
    home = '<html><a href="/escaping-the-dark-forest/">a</a><a href="/booby-trapping-the-ethereum-blockchain/">b</a></html>'
    respx.get("https://samczsun.com/").respond(200, text=home)
    respx.get("https://samczsun.com/escaping-the-dark-forest/").respond(
        200,
        text="<html><title>Escaping the Dark Forest - samczsun</title><article><p>First post body.</p></article></html>",
    )
    respx.get("https://samczsun.com/booby-trapping-the-ethereum-blockchain/").respond(
        200,
        text="<html><title>Booby Trapping - samczsun</title><article><p>Second post body.</p></article></html>",
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "research" for d in docs)
    assert all(d.metadata["collection"] == "research_v2" for d in docs)
    assert docs[0].doc_id == "samczsun_blog:escaping-the-dark-forest"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── blocksecteam/phishing_contract_sigmetrics25 ──────────────────────────


def test_blocksecteam_phishing_parse() -> None:
    log_text = "0x000000000000232e765d898b5f1b189b5c993f05\n0x0000000000958ec6667eb97e2f64f6909073e6b7\nnot-an-address\n"
    addrs = blocksecteam_phishing.parse_address_log(log_text)
    assert len(addrs) == 2
    assert addrs[0] == "0x000000000000232e765d898b5f1b189b5c993f05"


@respx.mock
async def test_blocksecteam_phishing_fetch_mocked() -> None:
    src = mod.get_source("blocksecteam_phishing_contracts")
    assert src is not None
    respx.get(blocksecteam_phishing.LOG_URL).respond(
        200,
        text="0x000000000000232e765d898b5f1b189b5c993f05\n0x0000000000958ec6667eb97e2f64f6909073e6b7\n",
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "scam_entity" for d in docs)
    assert all(d.metadata["collection"] == "scam_intel_v2" for d in docs)
    assert docs[0].metadata["labels"] == ["drainer", "phishing"]
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── ossf/malicious-packages ──────────────────────────────────────────────


def test_ossf_osv_to_doc() -> None:
    advisory = {
        "id": "MAL-2023-8429",
        "summary": "Malicious code in littest (crates.io)",
        "details": "The package communicates with a malicious domain.",
        "affected": [{"package": {"ecosystem": "crates.io", "name": "littest"}}],
        "aliases": ["GHSA-abc"],
    }
    doc = ossf_malicious.osv_to_doc("osv/malicious/crates.io/littest/MAL-2023-8429.json", advisory)
    assert doc is not None
    assert doc.doc_type == "vulnerability_pattern"
    assert doc.metadata["collection"] == "crypto_vulnerabilities"
    assert doc.metadata["advisory_id"] == "MAL-2023-8429"
    assert doc.metadata["ecosystem"] == "crates.io"
    assert doc.metadata["package"] == "littest"
    assert doc.doc_id == "ossf_malicious_packages:MAL-2023-8429"
    assert ossf_malicious.osv_to_doc("x.json", {}) is None


@respx.mock
async def test_ossf_malicious_fetch_mocked() -> None:
    src = mod.get_source("ossf_malicious_packages")
    assert src is not None
    tree_url = "https://api.github.com/repos/ossf/malicious-packages/git/trees/main"
    respx.get(f"{tree_url}?recursive=1").respond(
        200,
        json={
            "tree": [
                {
                    "path": "osv/malicious/crates.io/littest/MAL-2023-8429.json",
                    "type": "blob",
                    "size": 100,
                },
                {
                    "path": "osv/malicious/git/github.com/x/MAL-2025-1.json",
                    "type": "blob",
                    "size": 100,
                },
            ]
        },
    )
    respx.get(
        "https://raw.githubusercontent.com/ossf/malicious-packages/main/osv/malicious/crates.io/littest/MAL-2023-8429.json"
    ).respond(
        200,
        json={
            "id": "MAL-2023-8429",
            "summary": "Malicious code in littest",
            "affected": [{"package": {"ecosystem": "crates.io", "name": "littest"}}],
        },
    )
    respx.get(
        "https://raw.githubusercontent.com/ossf/malicious-packages/main/osv/malicious/git/github.com/x/MAL-2025-1.json"
    ).respond(
        200,
        json={
            "id": "MAL-2025-1",
            "summary": "Key theft in solana package",
            "affected": [{"package": {"ecosystem": "npm", "name": "solana-crypto-validator"}}],
        },
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "vulnerability_pattern" for d in docs)
    assert all(d.metadata["collection"] == "crypto_vulnerabilities" for d in docs)
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── RSS feeds (feeds.py) ─────────────────────────────────────────────────


_RSS_FIXTURE = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item><title>Post One</title><link>https://example.com/one</link><pubDate>Mon, 01 Aug 2026 12:00:00 +0000</pubDate><description>First summary.</description></item>
<item><title>Post Two</title><link>https://example.com/two</link><pubDate>Tue, 02 Aug 2026 12:00:00 +0000</pubDate><description>Second summary.</description></item>
</channel></rss>"""


def test_rss_item_to_doc() -> None:
    doc = feeds.rss_item_to_doc(
        "trailofbits_blog_rss",
        {
            "title": "Post One",
            "url": "https://example.com/one",
            "published": "2026-08-01T12:00:00+00:00",
            "summary": "First summary.",
        },
        "research",
        "research_v2",
    )
    assert doc.doc_type == "research"
    assert doc.metadata["collection"] == "research_v2"
    assert doc.metadata["published"] == "2026-08-01T12:00:00+00:00"
    assert doc.doc_id.startswith("trailofbits_blog_rss:post-one:")


@respx.mock
async def test_trailofbits_blog_rss_fetch_mocked() -> None:
    src = mod.get_source("trailofbits_blog_rss")
    assert src is not None
    respx.get("https://blog.trailofbits.com/feed/").respond(200, text=_RSS_FIXTURE)
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "research" for d in docs)
    assert all(d.metadata["collection"] == "research_v2" for d in docs)
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


@respx.mock
async def test_zellic_blog_rss_fetch_mocked() -> None:
    src = mod.get_source("zellic_blog_rss")
    assert src is not None
    respx.get("https://www.zellic.io/blog/rss.xml").respond(200, text=_RSS_FIXTURE)
    docs = await src.fetch(limit=5)
    assert len(docs) == 2
    assert all(d.metadata["collection"] == "research_v2" for d in docs)


@respx.mock
async def test_immunefi_blog_rss_fetch_mocked() -> None:
    src = mod.get_source("immunefi_blog_rss")
    assert src is not None
    respx.get("https://immunefi.com/blog/rss/").respond(200, text=_RSS_FIXTURE)
    docs = await src.fetch(limit=5)
    assert len(docs) == 2
    assert all(d.metadata["collection"] == "research_v2" for d in docs)


@respx.mock
async def test_neodyme_blog_rss_fetch_mocked() -> None:
    src = mod.get_source("neodyme_blog_rss")
    assert src is not None
    respx.get("https://neodyme.io/rss.xml").respond(200, text=_RSS_FIXTURE)
    docs = await src.fetch(limit=5)
    assert len(docs) == 2
    assert all(d.metadata["collection"] == "research_v2" for d in docs)


@respx.mock
async def test_cftc_press_releases_rss_fetch_mocked() -> None:
    src = mod.get_source("cftc_press_releases_rss")
    assert src is not None
    respx.get("https://www.cftc.gov/PressRoom/PressReleases/PressReleasesRSS").respond(
        200, text=_RSS_FIXTURE
    )
    docs = await src.fetch(limit=5)
    assert len(docs) == 2
    assert all(d.doc_type == "enforcement_action" for d in docs)
    assert all(d.metadata["collection"] == "regulation_enforcement" for d in docs)


@respx.mock
async def test_rss_fetch_graceful_on_error() -> None:
    src = mod.get_source("trailofbits_blog_rss")
    assert src is not None
    respx.get("https://blog.trailofbits.com/feed/").respond(500)
    docs = await src.fetch(limit=5)
    assert docs == []


# ── CISA KEV ─────────────────────────────────────────────────────────────


def test_cisa_kev_to_doc() -> None:
    vuln = {
        "cveID": "CVE-2026-20349",
        "vendorProject": "Cisco",
        "product": "Firewall",
        "vulnerabilityName": "Heap Inspection",
        "shortDescription": "RCE in firewall.",
        "dateAdded": "2026-08-01",
        "dueDate": "2026-08-22",
        "knownRansomwareCampaignUse": "Known",
        "requiredAction": "Apply mitigations.",
    }
    doc = cisa_kev.kev_to_doc(vuln)
    assert doc is not None
    assert doc.doc_type == "vulnerability_pattern"
    assert doc.metadata["collection"] == "crypto_vulnerabilities"
    assert doc.metadata["cve_id"] == "CVE-2026-20349"
    assert doc.metadata["known_ransomware_campaign"] is True
    assert doc.doc_id == "cisa_kev:CVE-2026-20349"
    assert cisa_kev.kev_to_doc({"product": "x"}) is None


@respx.mock
async def test_cisa_kev_fetch_mocked() -> None:
    src = mod.get_source("cisa_kev")
    assert src is not None
    respx.get(cisa_kev.KEV_URL).respond(
        200,
        json={
            "vulnerabilities": [
                {
                    "cveID": "CVE-2026-1",
                    "vendorProject": "V",
                    "product": "P",
                    "vulnerabilityName": "N1",
                    "dateAdded": "2026-08-01",
                },
                {
                    "cveID": "CVE-2026-2",
                    "vendorProject": "V",
                    "product": "P",
                    "vulnerabilityName": "N2",
                    "dateAdded": "2026-08-02",
                },
            ]
        },
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "vulnerability_pattern" for d in docs)
    assert all(d.metadata["collection"] == "crypto_vulnerabilities" for d in docs)
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── openphish feed ───────────────────────────────────────────────────────


def test_openphish_parse_lines() -> None:
    urls = openphish.parse_feed_lines("https://a.com/\n# comment\nhttps://b.com/x\n<html>\n")
    assert urls == ["https://a.com/", "https://b.com/x"]


@respx.mock
async def test_openphish_fetch_mocked() -> None:
    src = mod.get_source("openphish_feed")
    assert src is not None
    respx.get("https://openphish.com/feed.txt").respond(
        200, text="https://m.lose-dafa.com/\nhttps://n3b9c37.com:8989/register.html\n"
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "scam_entity" for d in docs)
    assert all(d.metadata["collection"] == "scam_intel_v2" for d in docs)
    assert docs[0].metadata["entity"] == "m.lose-dafa.com"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── URLhaus CSV feed ─────────────────────────────────────────────────────


def test_urlhaus_row_to_doc_filters_threats() -> None:
    keep = [
        "1",
        "2026-08-16 05:07:07",
        "http://phish.example/x",
        "online",
        "",
        "phishing",
        "crypto",
        "https://urlhaus.abuse.ch/url/1/",
    ]
    drop = [
        "2",
        "2026-08-16 05:07:07",
        "http://mal.example/x",
        "online",
        "",
        "malware_download",
        "",
        "https://urlhaus.abuse.ch/url/2/",
    ]
    doc = urlhaus.urlhaus_row_to_doc(keep)
    assert doc is not None
    assert doc.metadata["collection"] == "scam_intel_v2"
    assert doc.metadata["labels"] == ["phishing"]
    assert urlhaus.urlhaus_row_to_doc(drop) is None
    assert urlhaus.urlhaus_row_to_doc([]) is None


def test_urlhaus_parse_csv_rows() -> None:
    text = (
        "################################################################\n"
        "# abuse.ch URLhaus Database Dump\n"
        '"1","2026-08-16 05:07:07","http://phish.example/x","online","","phishing","crypto","https://urlhaus.abuse.ch/url/1/"\n'
    )
    rows = urlhaus.parse_csv_rows(text)
    assert len(rows) == 1
    assert rows[0][0] == "1"


@respx.mock
async def test_urlhaus_fetch_mocked() -> None:
    src = mod.get_source("urlhaus_feed")
    assert src is not None
    csv_text = (
        "################################################################\n"
        '"1","2026-08-16 05:07:07","http://phish.example/x","online","","phishing","crypto","https://urlhaus.abuse.ch/url/1/"\n'
        '"2","2026-08-16 05:07:07","http://miner.example/y","online","","crypto_mining","cryptominer","https://urlhaus.abuse.ch/url/2/"\n'
        '"3","2026-08-16 05:07:07","http://mal.example/z","online","","malware_download","","https://urlhaus.abuse.ch/url/3/"\n'
    )
    respx.get(urlhaus.FEED_URL).respond(200, text=csv_text)
    docs = await src.fetch(limit=10)
    assert len(docs) == 2  # malware_download filtered out
    assert all(d.doc_type == "scam_entity" for d in docs)
    assert all(d.metadata["collection"] == "scam_intel_v2" for d in docs)
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── yAudit/defi-fork-bugs ────────────────────────────────────────────────


def test_yaudit_parse_fork_bugs() -> None:
    md = (
        "# Common fork bugs\n\n"
        "## Compound\n\n"
        "- CREAM: flashloan attack & reentrancy [Postmortem](https://medium.com/x) [POC](https://github.com/y)\n"
        "- Lendf.me: Flashloan and reentrancy [Postmortem](https://medium.com/z)\n\n"
        "## Uniswap\n\n"
        "- Foo: price manipulation [Thread](https://twitter.com/t)\n"
    )
    bugs = yaudit_fork_bugs.parse_fork_bugs(md)
    assert len(bugs) == 3
    assert bugs[0].protocol == "Compound"
    assert "flashloan" in bugs[0].description.lower()
    assert bugs[0].links[0] == ("Postmortem", "https://medium.com/x")
    assert bugs[2].protocol == "Uniswap"


def test_yaudit_fork_bug_to_doc() -> None:
    doc = yaudit_fork_bugs.fork_bug_to_doc(
        yaudit_fork_bugs.ForkBug(
            "Compound", "flashloan & reentrancy", (("Postmortem", "https://medium.com/x"),)
        ),
        0,
    )
    assert doc.doc_type == "post_mortem"
    assert doc.metadata["collection"] == "defi_exploits"
    assert doc.metadata["protocol"] == "Compound"
    assert doc.doc_id.startswith("yaudit_defi_fork_bugs:compound:")


@respx.mock
async def test_yaudit_fetch_mocked() -> None:
    src = mod.get_source("yaudit_defi_fork_bugs")
    assert src is not None
    respx.get(yaudit_fork_bugs.README_URL).respond(
        200,
        text="## Compound\n- CREAM: flashloan [Postmortem](https://medium.com/x)\n## Aave\n- Foo: oracle issue [Postmortem](https://medium.com/y)\n",
    )
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "post_mortem" for d in docs)
    assert all(d.metadata["collection"] == "defi_exploits" for d in docs)
    assert docs[0].metadata["protocol"] == "Compound"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]


# ── coinspect/learn-evm-attacks ──────────────────────────────────────────


def test_coinspect_readme_to_doc() -> None:
    doc = coinspect_evm_attacks.readme_to_doc(
        "test/Access_Control/DAOMaker/README.md",
        "# DAOMaker\n\nVulnerability description and PoC details.\n",
    )
    assert doc is not None
    assert doc.doc_type == "exploit_poc"
    assert doc.metadata["collection"] == "defi_exploits"
    assert doc.metadata["protocol"] == "DAOMaker"
    assert doc.metadata["vulnerability_class"] == "access-control"
    assert doc.doc_id == "coinspect_learn_evm_attacks:test/Access_Control/DAOMaker/README.md"
    assert coinspect_evm_attacks.readme_to_doc("README.md", "x") is None
    assert (
        coinspect_evm_attacks.readme_to_doc("test/Access_Control/DAOMaker/README.md", "   ") is None
    )


@respx.mock
async def test_coinspect_fetch_mocked() -> None:
    src = mod.get_source("coinspect_learn_evm_attacks")
    assert src is not None
    tree_url = "https://api.github.com/repos/coinspect/learn-evm-attacks/git/trees/master"
    respx.get(f"{tree_url}?recursive=1").respond(
        200,
        json={
            "tree": [
                {"path": "test/Access_Control/DAOMaker/README.md", "type": "blob", "size": 100},
                {"path": "test/Bad_Data_Validation/1inch/README.md", "type": "blob", "size": 100},
            ]
        },
    )
    respx.get(
        "https://raw.githubusercontent.com/coinspect/learn-evm-attacks/master/test/Access_Control/DAOMaker/README.md"
    ).respond(200, text="# DAOMaker\n\nAccess control bug details.\n")
    respx.get(
        "https://raw.githubusercontent.com/coinspect/learn-evm-attacks/master/test/Bad_Data_Validation/1inch/README.md"
    ).respond(200, text="# 1inch\n\nBad data validation details.\n")
    docs = await src.fetch(limit=10)
    assert len(docs) == 2
    assert all(d.doc_type == "exploit_poc" for d in docs)
    assert all(d.metadata["collection"] == "defi_exploits" for d in docs)
    assert docs[0].metadata["vulnerability_class"] == "access-control"
    docs2 = await src.fetch(limit=10)
    assert [d.doc_id for d in docs] == [d.doc_id for d in docs2]
