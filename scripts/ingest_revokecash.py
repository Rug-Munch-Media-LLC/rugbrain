#!/usr/bin/env python3
"""Ingest Revoke.cash exploit knowledge into the ``threat_intel_v2`` collection.

Source: Revoke.cash (https://revoke.cash) — a token-approval management
service that also maintains a well-known list of approval-based exploits
and 'revoke after use' security guidance. The script ships a curated
dataset of approval-exploit case studies and approval-safety rules so
RugBrain can explain approval-drainer attacks and remediation.

Usage:
    python scripts/ingest_revokecash.py --dry-run
    python scripts/ingest_revokecash.py --limit 20
    python scripts/ingest_revokecash.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - Infinity at INFINITY_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_revokecash")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import build_doc, write_docs  # noqa: E402

COLLECTION = "threat_intel_v2"
SOURCE = "revokecash"

# ── Curated Revoke.cash exploit knowledge ───────────────────────────────
REVOKECASH_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "rel_key": "revokecash:guideline:approval-hygiene",
        "title": "Revoke.cash Guideline: Approval Hygiene",
        "category": "approval-exploit",
        "severity": "high",
        "chain": "multi",
        "text": (
            "Revoke.cash guideline: approval hygiene. ERC20 approvals grant a "
            "spender the right to move tokens until revoked. Unlimited "
            "approvals (type(uint256).max) never expire and remain a standing "
            "risk if the spender contract is compromised or malicious. "
            "Revoke.cash recommends: use limited/one-time approvals where "
            "possible; revoke approvals to contracts you no longer use; "
            "periodically audit the approvals list; and treat every approval "
            "as a potential drain vector. Revoking an approval is a "
            "zero-cost transaction that removes the exposure."
        ),
        "source_url": "https://revoke.cash",
        "ecosystem": "multi",
    },
    {
        "rel_key": "revokecash:exploit:approval-phishing",
        "title": "Revoke.cash Exploit: Approval Phishing",
        "category": "phishing",
        "severity": "critical",
        "chain": "multi",
        "text": (
            "Revoke.cash exploit: approval phishing. Attackers create fake "
            "dApps, fake airdrops, or fake NFT mints that prompt users to sign "
            "an approval transaction (often unlimited). Once signed, the "
            "attacker's address can transfer all approved tokens at will. "
            "This is the most common wallet-drain vector on EVM chains. "
            "Red flags: unexpected approval requests, approval to an unknown "
            "contract, unlimited amounts, and requests embedded in 'claim' "
            "or 'verify' flows. Revoke.cash's approval dashboard exists "
            "precisely to let users find and revoke such approvals."
        ),
        "source_url": "https://revoke.cash/exploits",
        "ecosystem": "multi",
    },
    {
        "rel_key": "revokecash:exploit:permit-signature",
        "title": "Revoke.cash Exploit: Permit Signature Theft",
        "category": "signature-malleability",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "Revoke.cash exploit: permit signature theft. ERC-2612 permit "
            "lets a user sign an off-chain approval that anyone can submit. "
            "Phishing sites trick users into signing permit payloads for "
            "malicious contracts, which then call permit() to obtain spend "
            "authority without any on-chain approval transaction. Users who "
            "reject permit requests and verify the exact contract address "
            "being approved avoid this vector. Revoke.cash shows both "
            "approvals and open permits in its dashboard."
        ),
        "source_url": "https://revoke.cash/exploits",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "revokecash:exploit:compromised-spender",
        "title": "Revoke.cash Exploit: Compromised Spender Contract",
        "category": "approval-exploit",
        "severity": "high",
        "chain": "multi",
        "text": (
            "Revoke.cash exploit: compromised spender contract. A legitimate "
            "protocol you approved (DEX router, aggregator, lending market) "
            "is exploited or upgraded to malicious code, and the attacker uses "
            "the existing approvals to drain user balances. Notable examples: "
            "Bancor v1 (2020), Agave/Hundred Finance (2022), and various "
            "router compromises. Because the approval persists, users are "
            "exposed even if they no longer use the protocol. Revoking "
            "approvals to little-used contracts is the recommended defense."
        ),
        "source_url": "https://revoke.cash/exploits",
        "ecosystem": "multi",
    },
    {
        "rel_key": "revokecash:exploit:erc721-approval",
        "title": "Revoke.cash Exploit: NFT Approval Drain",
        "category": "approval-exploit",
        "severity": "high",
        "chain": "ethereum",
        "text": (
            "Revoke.cash exploit: NFT approval drain. ERC-721 setApprovalForAll "
            "grants a marketplace or malicious contract control over all NFTs "
            "in a wallet. Phishing sites ask users to approve 'marketplace "
            "access' for a fake listing; the approval then lets the attacker "
            "transfer any NFT. Revoke.cash tracks both token and NFT "
            "approvals and warns users to only approve trusted marketplaces "
            "with verified domains and contracts."
        ),
        "source_url": "https://revoke.cash/exploits",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "revokecash:exploit:uniswap-permit2",
        "title": "Revoke.cash Exploit: Permit2 Phishing (2024)",
        "category": "approval-exploit",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "Revoke.cash exploit: Uniswap Permit2 phishing (2024). Attackers "
            "targeted the Permit2 allowance contract, which stores "
            "signature-based allowances that apply across Uniswap and many "
            "integrations. Phishing sites obtained permit signatures granting "
            "allowance to attacker-controlled spender contracts, then drained "
            "tokens. The attack highlighted that signature-based allowances "
            "(Permit2) are as dangerous as on-chain approvals. Users should "
            "verify spender addresses and use allowance limits, and revoke "
            "Permit2 allowances through the official interface."
        ),
        "source_url": "https://revoke.cash/exploits",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "revokecash:method:revoke-checklist",
        "title": "Revoke.cash Revocation Checklist",
        "category": "methodology",
        "severity": "info",
        "chain": "multi",
        "text": (
            "Revoke.cash revocation checklist. (1) Identify approvals: use an "
            "approval explorer (revoke.cash, etherscan) to list all approved "
            "spenders. (2) Prioritize: revoke unlimited approvals to "
            "unfamiliar or unused contracts first. (3) For active protocols, "
            "consider reducing the allowance to a small amount instead of "
            "unlimited. (4) Revoke ERC-721/1155 setApprovalForAll entries you "
            "no longer use. (5) After the revoke transaction confirms, "
            "re-check the list. (6) Beware 'revoke' phishing sites that ask "
            "for a signature — only use trusted interfaces."
        ),
        "source_url": "https://revoke.cash",
        "ecosystem": "multi",
    },
    {
        "rel_key": "revokecash:method:detect-drainer",
        "title": "Detecting Approval-Drainer Scams (RugBrain integration)",
        "category": "methodology",
        "severity": "info",
        "chain": "multi",
        "text": (
            "Detecting approval-drainer scams. On-chain signals: the token or "
            "dApp requests approval (approve or permit) to a contract that is "
            "not the well-known router/marketplace; the approval amount is "
            "type(uint256).max; the spender contract is newly deployed, has "
            "no verified source, or its functions can transferFrom arbitrary "
            "addresses. RugBrain correlates these signals with Revoke.cash "
            "knowledge and wallet-drainer heuristics to flag "
            "approval-drainer risk before a user signs."
        ),
        "source_url": "https://revoke.cash",
        "ecosystem": "multi",
    },
]


def _docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in REVOKECASH_KNOWLEDGE:
        metadata = {
            "title": entry["title"],
            "category": entry["category"],
            "ecosystem": entry.get("ecosystem", "multi"),
            "knowledge_type": "revokecash",
            "source": SOURCE,
            "source_url": entry.get("source_url", "https://revoke.cash"),
            "protocol": entry.get("title", ""),
            "attack_vector": entry["category"],
        }
        docs.append(
            build_doc(
                source=SOURCE,
                rel_key=entry["rel_key"],
                content=entry["text"],
                metadata=metadata,
                severity=entry.get("severity", "medium"),
                chain=entry.get("chain", "multi"),
                collection=COLLECTION,
            )
        )
    return docs


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    docs = _docs()
    if args.limit:
        docs = docs[: args.limit]

    store = QdrantVectorStore()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            stats = await write_docs(
                client,
                store,
                docs,
                source=SOURCE,
                collection=COLLECTION,
                dry_run=args.dry_run,
            )
    finally:
        await store.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Revoke.cash approval-exploit knowledge into threat_intel_v2"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to ingest")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"REVOKE.CASH INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
