#!/usr/bin/env python3
"""Protocol registry for ``ingest_codebase_knowledge.py``.

Defines which public smart-contract codebases RugBrain ingests as
"known good reference implementations", how to collect their source files,
and the curated vulnerability history attached to each protocol so retrieval
can answer "what happened to this codebase?".

Languages:
    solidity — EVM contracts, ingested into the ``solidity_patterns`` collection
    rust     — Solana programs, ingested into the ``solana_patterns`` collection

Repos are cloned shallow (--depth 1) into the configured repo directory and
never committed into this repository (AGENTS.md: no data blobs, no nested
repos).
"""

from __future__ import annotations

from typing import Any

PROTOCOLS: list[dict[str, Any]] = [
    {
        "id": "openzeppelin",
        "name": "OpenZeppelin Contracts",
        "repo": "https://github.com/OpenZeppelin/openzeppelin-contracts.git",
        "tag": "v5.2.0",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("contracts/",),
        "exclude_dirs": ("mock", "mocks", "test", "tests", "examples", "scripts"),
        "vulnerability_history": (
            "Gold-standard Solidity library. No critical vulnerabilities in "
            "v5.x; historically audited by multiple firms (OpenZeppelin, Trail "
            "of Bits, Consensys). Early v4.x had a UUPS/ERC1967 upgrade edge "
            "case and a governance timelock pitfall; always use the latest "
            "patch. Reference for ERC20/ERC721/ERC1155, access control, proxy "
            "and upgradeable contracts."
        ),
    },
    {
        "id": "spl",
        "name": "Solana Program Library",
        "repo": "https://github.com/solana-labs/solana-program-library.git",
        "tag": "v1.18.0",
        "language": "rust",
        "collection": "solana_patterns",
        "verdict": "good",
        "include": (
            "token/program/src",
            "token/2022/src",
            "token/associated-token-account/program/src",
            "token/stake-pool/program/src",
            "account-compression/programs",
        ),
        "exclude_dirs": ("test", "tests", "bench", "benches", "examples", "js"),
        "vulnerability_history": (
            "Official Solana programs (SPL token, token-2022, ATA, stake-pool, "
            "account-compression). The core token program is battle-tested; "
            "token-2022 extensions (transfer hooks, permanent delegate, "
            "confidential transfers) add new attack surface — several 2024/2025 "
            "exploits abused transfer-hook and permanent-delegate setups in "
            "tokens built on 2022 extensions."
        ),
    },
    {
        "id": "uniswap-v2",
        "name": "Uniswap V2",
        "repo": "https://github.com/Uniswap/v2-core.git",
        "tag": "v2.0.1",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("contracts/",),
        "exclude_dirs": ("test", "tests", "examples"),
        "vulnerability_history": (
            "Most-forked DEX codebase. Core is minimal and audited; no major "
            "exploits of v2-core itself. Hundreds of rug-pull forks modified "
            "the fee/tax logic or added backdoors — a fork of Uniswap v2 is "
            "the canonical starting point for honeypots and fee-manipulation "
            "scams. Periphery (router) gained fee-on-transfer hardening after "
            "early issues."
        ),
    },
    {
        "id": "uniswap-v3",
        "name": "Uniswap V3",
        "repo": "https://github.com/Uniswap/v3-core.git",
        "tag": "v1.0.0",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("contracts/",),
        "exclude_dirs": ("test", "tests", "examples"),
        "vulnerability_history": (
            "Concentrated-liquidity AMM. Extensively audited (ABDK, Trail of "
            "Bits); core has been stable. Complexity of tick math and position "
            "management is a rich source of integration bugs in protocols "
            "built on v3 rather than in v3 itself."
        ),
    },
    {
        "id": "uniswap-v4",
        "name": "Uniswap V4 Core",
        "repo": "https://github.com/Uniswap/v4-core.git",
        "tag": "v4.0.0",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("src/",),
        "exclude_dirs": ("test", "tests", "examples", "script", "scripts"),
        "vulnerability_history": (
            "Hooks-based AMM (singleton + flash accounting). Audited by "
            "multiple firms across 2023-2024. The hook model introduces a "
            "large new attack surface: hooks execute arbitrary code during "
            "swaps, so hook-enabled pools must validate hook permissions and "
            "reentrancy."
        ),
    },
    {
        "id": "aave-v3",
        "name": "Aave V3 Core",
        "repo": "https://github.com/aave/aave-v3-core.git",
        "tag": "v1.19.2",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("src/",),
        "exclude_dirs": ("test", "tests", "examples", "mocks"),
        "vulnerability_history": (
            "Lending-protocol reference (liquidation, isolation mode, e-mode). "
            "Audited by multiple firms; the 2022 'Aave v2 CRITICAL' finding "
            "(unbounded token transfer) was disclosed and patched before "
            "exploitation. Governance risk: Aave's risk parameters are set by "
            "governance, and oracle/parameter attacks against forks are common."
        ),
    },
    {
        "id": "compound",
        "name": "Compound Protocol",
        "repo": "https://github.com/compound-finance/compound-protocol.git",
        "tag": "v2.8.1",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("contracts/",),
        "exclude_dirs": ("test", "tests", "examples", "mocks"),
        "vulnerability_history": (
            "CToken-based lending (Comet v3 successor). v2 is battle-tested "
            "but has known edge cases: the 2021 COMP distribution bug "
            "(incorrect reward accrual) and oracle/asset-listing governance "
            "risks. Compound forks are frequently exploited via bad "
            "collateral assets and manipulated price feeds."
        ),
    },
    {
        "id": "chainlink",
        "name": "Chainlink Solidity Contracts",
        "repo": "https://github.com/smartcontractkit/chainlink.git",
        "tag": "v2.11.0",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("contracts/src/v0.8/",),
        "exclude_dirs": ("test", "tests", "examples", "mocks", "dev", "scripts"),
        "vulnerability_history": (
            "Oracle reference implementation: AggregatorV3Interface, "
            "VRF, Keepers/Automation, feeds. The on-chain contracts are "
            "battle-tested; the historic risk surface is off-chain "
            "(node operators, multisig keys) and protocols misusing feeds "
            "(e.g. single-source price without TWAP/liveness checks)."
        ),
    },
    {
        "id": "eigenlayer",
        "name": "EigenLayer Contracts",
        "repo": "https://github.com/Layr-Labs/eigenlayer-contracts.git",
        "tag": "v0.5.0",
        "language": "solidity",
        "collection": "solidity_patterns",
        "verdict": "good",
        "include": ("src/contracts/",),
        "exclude_dirs": ("test", "tests", "examples", "mock", "mocks", "scripts"),
        "vulnerability_history": (
            "Restaking reference (StrategyManager, DelegationManager, "
            "EigenPod, slashing). Undergone multiple audit rounds "
            "(2023-2025); the April 2025 EIGEN token theft (~$5.5M) exploited "
            "a governance-token vulnerability in a separate token contract, "
            "highlighting that restaking primitives must validate operator "
            "delegation and withdrawal credentials carefully."
        ),
    },
]
