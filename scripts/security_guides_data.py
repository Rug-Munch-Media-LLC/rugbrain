#!/usr/bin/env python3
"""Curated security-audit guides for ``ingest_security_guides.py``.

Distilled from the Solcurity checklist (Rari-Capital/solcurity), Consensys
Smart Contract Best Practices, the Trail of Bits / chainflag Awesome Blockchain
Security lists, and DeFi hack analysis write-ups. These short docs make the
coding-knowledge collection answer "how do I audit / secure a contract" style
questions even when live fetching is unavailable.
"""

from __future__ import annotations

from typing import Any

SOLCURITY_DOCS: list[dict[str, Any]] = [
    {
        "source": "solcurity",
        "rel_key": "checklist/solidity",
        "title": "Solcurity — Solidity security checklist (condensed)",
        "topic": "audit-checklist",
        "knowledge_type": "checklist",
        "content": (
            "Solcurity — the condensed Solidity security review checklist: "
            "1) External calls: use CEI (Checks-Effects-Interactions); consider "
            "reentrancy guards; revalidate state after calls; check return values. "
            "2) Access control: every state-changing function needs an explicit "
            "authorization; use msg.sender not tx.origin; admin keys are multisig. "
            "3) Arithmetic: watch overflow/underflow (0.8+ checked by default); "
            "rounding directions matter (favor user); avoid price/rate "
            "manipulation. 4) Tokens: handle fee-on-transfer and rebasing tokens; "
            "validate token addresses and return values (SafeERC20). "
            "5) Storage: check upgradeable storage layout; beware delegatecall; "
            "watch selfdestruct. 6) Randomness/oracles: never trust "
            "block.timestamp/blockhash; use VRF/commit-reveal; check oracle "
            "staleness. 7) Gas: unbounded loops, storage writes in loops, denial-"
            "of-service via gas. 8) Informational: NATSPEC docs, event emissions, "
            "error handling on every external call."
        ),
    },
    {
        "source": "solcurity",
        "rel_key": "checklist/common-bugs",
        "title": "Solcurity — common bug classes to look for",
        "topic": "audit-checklist",
        "knowledge_type": "checklist",
        "content": (
            "Bug classes the Solcurity checklist is designed to catch: "
            "- Reentrancy (single + cross-function + read-only) — look for "
            "external calls before state updates, missing guards. "
            "- Access control gaps — functions callable by anyone that should be "
            "admin-only; tx.origin checks. "
            "- Integer overflow/underflow and rounding errors — division before "
            "multiplication loses precision. "
            "- Oracle manipulation — using spot price from an AMM you can "
            "manipulate (use TWAP, Chainlink). "
            "- Flash-loan attacks — any function whose outcome depends on "
            "liquidity can be attacked with a flash loan. "
            "- Frontrunning — order-dependent state (last-highest-bid, "
            "first-come), use commit-reveal or MEV-resistant designs. "
            "- Unbounded loops / gas griefing — attacker controls loop length. "
            "- Denial of service — arrays that grow unboundedly, funds stuck on "
            "revert. "
            "- Signature malleability — ecrecover without low-s enforcement, "
            "missing nonces. "
            "- Proxy/upgrade bugs — storage collisions, uninitialized "
            "implementations. "
            "Cross-reference with SWC Registry IDs (SWC-100..136) for the "
            "canonical taxonomy."
        ),
    },
    {
        "source": "solcurity",
        "rel_key": "checklist/rust-solana",
        "title": "Solcurity — Solana/Rust checklist additions",
        "topic": "audit-checklist",
        "knowledge_type": "checklist",
        "content": (
            "When auditing Solana programs, add to the Solidity checklist: "
            "1) Account validation: owner check, key check (PDA derivation, "
            "has_one), signer check on every authority. 2) PDA: canonical bump "
            "only; validate all seeds. 3) CPI: program-id check before invoke; "
            "least-privilege account lists; revalidate after CPI. 4) Reentrancy "
            "across CPIs. 5) Arithmetic: use checked_mul/checked_add (u64 "
            "overflow in lamports/amounts). 6) Close-account race: zero data "
            "before refunding lamports. 7) Compute budget: avoid exceeding CU "
            "limit; keep loops bounded. 8) Token program: validate mint/owner "
            "fields on token accounts; beware Token-2022 differences. "
            "Use anchor constraints to enforce most of these automatically."
        ),
    },
]

CONSENSYS_DOCS: list[dict[str, Any]] = [
    {
        "source": "consensys-best-practices",
        "rel_key": "general/known-attacks",
        "title": "Consensys best practices — known attacks overview",
        "topic": "security",
        "knowledge_type": "best_practice",
        "content": (
            "Consensys Smart Contract Best Practices catalog the known attack "
            "classes: reentrancy (incl. cross-function and read-only), "
            "integer overflow/underflow, denial of service (unexpected revert, "
            "block gas limit, owner privileges), front-running, timestamp "
            "dependence, tx.origin authorization, force-feeding ether (selfdestruct "
            "into a contract that can't handle it), unexpected ether (receive/"
            "fallback), delegatecall misuse, and honeypots. Recommended "
            "defenses: use OpenZeppelin audited base contracts, prefer pull "
            "payments, use CEI, add reentrancy guards, cap loops, use "
            "commit-reveal for order-dependent logic, and monitor with "
            "on-chain watchdogs. Always document failure modes and keep a "
            "circuit breaker for emergencies."
        ),
    },
    {
        "source": "consensys-best-practices",
        "rel_key": "general/development",
        "title": "Consensys best practices — development recommendations",
        "topic": "security",
        "knowledge_type": "best_practice",
        "content": (
            "Consensys development recommendations: 1) Prepare for failure — "
            "circuit breakers, speed bumps (delay), rate limits, and a "
            "kill-switch. 2) Roll out carefully — phased deployment, alerting, "
            "bug bounties before launch. 3) Restrict privileges — deny-by-default, "
            "role-based access, multisig for admin. 4) Keep it simple — smaller "
            "surface area; reuse battle-tested code (OpenZeppelin, Solmate); "
            "avoid premature optimization. 5) Upgrade with care — test upgrades "
            "on testnet, keep upgradeability minimal, consider proxy-free "
            "migration. 6) Write tests + fuzz (Foundry, Echidna) and get "
            "independent audits. 7) Plan for on-chain monitoring and incident "
            "response."
        ),
    },
]

HACK_ANALYSIS_DOCS: list[dict[str, Any]] = [
    {
        "source": "defi-hack-analysis",
        "rel_key": "analysis/reentrancy-poc",
        "title": "DeFi hack analysis — reentrancy exploit anatomy (DAO, Euler-style)",
        "topic": "reentrancy",
        "knowledge_type": "hack_analysis",
        "content": (
            "Classic reentrancy exploits (the DAO 2016, Euler 2023, Platypus "
            "2023) share anatomy: a victim contract calls out to an attacker "
            "contract before updating its own state, and the attacker re-enters "
            "the victim's function with a callback. Euler's 2023 $197M exploit "
            "used a read-only reentrancy variant: the attacker re-entered during a "
            "donation call, corrupting the protocol's view of its own balances and "
            "letting them borrow against inflated collateral. Defenses: CEI, "
            "nonReentrant guards, and for read-only reentrancy, snapshot-based "
            "balance reads or guarding view functions that other protocols call. "
            "Audit check: find every external call (transfer, call, token "
            "transfer) and confirm the state update precedes it."
        ),
    },
    {
        "source": "defi-hack-analysis",
        "rel_key": "analysis/oracle-manipulation",
        "title": "DeFi hack analysis — oracle manipulation exploits",
        "topic": "oracle-manipulation",
        "knowledge_type": "hack_analysis",
        "content": (
            "Oracle manipulation hacks (bZx 2020, Harvest 2020, Cream 2021, "
            "Mango 2022) all abused spot prices that could be moved cheaply. "
            "Pattern: attacker flash-loans a large position, moves the pool price, "
            "borrows/liquidates against the distorted price, then unwinds. "
            "Mango Markets ($114M, 2022) manipulated the native MNGO oracle by "
            "building a large spot position in the oracle's underlying and "
            "borrowing against it. Defenses: use time-weighted average prices "
            "(TWAP) with a long window, Chainlink feeds with deviation "
            "thresholds, multiple independent oracles, and circuit breakers on "
            "price moves. Audit check: trace every price input to its source and "
            "calculate the cost to move it 1%."
        ),
    },
    {
        "source": "defi-hack-analysis",
        "rel_key": "analysis/flash-loan-attack",
        "title": "DeFi hack analysis — flash loan attack patterns",
        "topic": "flash-loan",
        "knowledge_type": "hack_analysis",
        "content": (
            "Flash loans let attackers borrow unlimited capital within one "
            "transaction, weaponizing protocol logic: price manipulation, "
            "arbitrage on mispriced liquidation, governance attacks (borrow "
            "tokens to vote), and vault draining via manipulated collateral "
            "values. Defenses: TWAP/Chainlink prices (not spot), mint/burn caps, "
            "debt-to-collateral ratio sanity checks, and invariant checks that "
            "hold regardless of capital size. Audit check: ask 'can an attacker "
            "obtain unlimited capital to exploit this function?' — if yes, the "
            "function must be robust to arbitrary capital."
        ),
    },
    {
        "source": "defi-hack-analysis",
        "rel_key": "analysis/governance-attacks",
        "title": "DeFi hack analysis — governance and access-control exploits",
        "topic": "access-control",
        "knowledge_type": "hack_analysis",
        "content": (
            "Governance exploits (Beanstalk $182M 2022, Compound 2023, many "
            "bridge multisigs) abuse privileged roles: flash-loaning governance "
            "tokens to pass malicious proposals, compromised admin keys, or "
            "proposal implementations that call attacker contracts. Beanstalk "
            "used a flash loan to buy governance voting power and pass a proposal "
            "that drained the treasury. Defenses: timelocks on governance "
            "actions (72h+), proposal execution without external-call hazards, "
            "flash-loan-resistant voting (weighted by held time), multisig with "
            "hardware keys for admin roles, and revoking unused privileges. "
            "Audit check: enumerate every privileged function and ask 'who can "
            "call this and what's the blast radius?'"
        ),
    },
]

ALL_SECURITY_GUIDE_DOCS: list[dict[str, Any]] = SOLCURITY_DOCS + CONSENSYS_DOCS + HACK_ANALYSIS_DOCS
