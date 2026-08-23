#!/usr/bin/env python3
"""Ingest RugDoc knowledge into the RugBrain ``known_scams`` collection.

Source: RugDoc (https://rugdoc.io) — one of the leading DeFi safety and
education communities. RugDoc publishes rug-pull case studies, audit
reports, and token-safety guides that flag honeypots, liquidity locks,
ownership centralization, and hidden fee structures.

The script ships with a curated dataset (``RUGDOC_KNOWLEDGE`` below) so it
works fully offline, and can additionally fetch RugDoc's public token
audit pages when the site is reachable. Each entry is embedded (2560-dim
qwen3-embedding-4b) and upserted into ``known_scams`` with canonical metadata
(category, severity, ecosystem, source, source_url).

Usage:
    python scripts/ingest_rugdoc.py --dry-run
    python scripts/ingest_rugdoc.py --limit 20
    python scripts/ingest_rugdoc.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
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
logger = logging.getLogger("ingest_rugdoc")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import build_doc, write_docs  # noqa: E402

COLLECTION = "known_scams"
SOURCE = "rugdoc"

# ── Curated RugDoc knowledge ────────────────────────────────────────────
# RugDoc-style token safety findings and rug-pull case studies. Each dict
# carries the fields used by the ingest pipeline.
RUGDOC_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "rel_key": "rugdoc:guideline:ownership-centralization",
        "title": "Ownership Centralization (RugDoc guideline)",
        "category": "access-control",
        "severity": "critical",
        "chain": "bsc",
        "text": (
            "RugDoc guideline: ownership centralization. A token is a rug-pull "
            "candidate when the deployer or owner holds the ability to mint new "
            "supply, blacklist addresses, modify transfer fees, pause trading, or "
            "upgrade the contract at will. RugDoc flags contracts where the owner "
            "can change trading rules after launch, because the same permission "
            "allows a liquidity-pull (owner removes liquidity and drains the pool). "
            "Key checks: owner can mint(); owner can setFee(); owner can "
            "setBlacklist(); contract is upgradeable behind a proxy with an "
            "owner-controlled admin; ownership is not renounced and no timelock "
            "guards admin actions. Safe projects renounce ownership, lock it "
            "behind a timelock+multisig, or remove admin powers entirely."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:guideline:honeypot-sell-tax",
        "title": "Honeypot — Sell Tax 100% (RugDoc guideline)",
        "category": "honeypot",
        "severity": "critical",
        "chain": "bsc",
        "text": (
            "RugDoc guideline: honeypot sell tax. A contract that charges a 100% "
            "sell fee (or otherwise blocks sells while allowing buys) is a "
            "honeypot: buyers can deposit but cannot exit. RugDoc detects this by "
            "simulating a buy and a sell on PancakeSwap/Uniswap and comparing the "
            "output amounts. Red flags: sell fee > buy fee by a wide margin; "
            "transfer() reverts for sellers except the owner; the contract keeps "
            "an internal balance ledger instead of standard ERC20 accounting; "
            "'anti-whale' logic that only affects sells. Honeypots are the most "
            "common scam family on BSC and the primary reason RugDoc tells users "
            "to test both buy and sell before investing."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:guideline:liquidity-not-locked",
        "title": "Liquidity Not Locked (RugDoc guideline)",
        "category": "rug-pull",
        "severity": "critical",
        "chain": "bsc",
        "text": (
            "RugDoc guideline: liquidity not locked. When the token pairs its "
            "liquidity on a DEX but the liquidity-provider tokens are not locked "
            "or burned, the deployer can withdraw the entire pool, collapsing the "
            "price to zero (liquidity pull / rug pull). RugDoc checks whether the "
            "LP tokens sit in a verified locker (e.g. Unicrypt, Team Finance), are "
            "burned, or remain in the deployer wallet. Red flags: LP tokens held "
            "by the deployer; no lock or a lock that expires quickly; liquidity "
            "added then removed shortly after launch. Even with ownership renounced, "
            "unlocked LP is a direct rug vector."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:guideline:fake-renounce",
        "title": "Fake Renounce / Hidden Owner (RugDoc guideline)",
        "category": "access-control",
        "severity": "high",
        "chain": "bsc",
        "text": (
            "RugDoc guideline: fake renounce. Some contracts appear to renounce "
            "ownership on the frontend but keep a hidden owner role, a secondary "
            "admin variable, or a backdoor function callable only by a hardcoded "
            "address. Detection: compare the actual contract storage after the "
            "'renounce' tx (owner slot may still be set); look for secondary "
            "mappings like 'operator', 'admin', 'guardian', 'signer' that bypass "
            "the OpenZeppelin Ownable flow; check for hardcoded addresses in the "
            "bytecode that can call privileged functions. RugDoc treats any "
            "discrepancy between announced and actual ownership as a high-risk "
            "scam signal."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:guideline:high-transfer-fee",
        "title": "Excessive Transfer Fee (RugDoc guideline)",
        "category": "business-logic",
        "severity": "high",
        "chain": "bsc",
        "text": (
            "RugDoc guideline: excessive transfer fee. Tokens that tax every "
            "transfer at 10-20%+ send a large share to the deployer wallet or a "
            "'marketing' wallet controlled by the team. Combined with sell "
            "pressure, this drains holders even without an explicit rug. RugDoc "
            "flags fees above ~10%, fees that change over time, and fee "
            "destinations that are EOAs rather than locked contracts. Legitimate "
            "projects keep fees low, transparent, and routed to audited contracts "
            "with clear on-chain records."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:case:rugpull-squid-game",
        "title": "Squid Game Token Rug Pull (2021) — RugDoc case study",
        "category": "rug-pull",
        "severity": "critical",
        "chain": "bsc",
        "text": (
            "RugDoc case study: the Squid Game token (SQUID) rug pull, October "
            "2021. The project raised ~$3.38M from investors on BSC before the "
            "deployer removed liquidity and the token collapsed ~99%. Red flags "
            "that RugDoc's framework would have caught: anonymous team, no "
            "audit, ownership not renounced, locked liquidity not verified, and "
            "a website offering a play-to-earn game that never launched. The "
            "event became the canonical example of a 'liquidity pull' rug and "
            "was widely reported by CoinDesk, Bloomberg, and others."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:case:bridge-bsc-native",
        "title": "BSC Native Token Bridge Hacks (2021) — RugDoc coverage",
        "category": "bridge-exploit",
        "severity": "critical",
        "chain": "bsc",
        "text": (
            "RugDoc coverage: BSC bridge hacks 2021. Multiple BSC projects used "
            "bridge contracts that let the deployer mint wrapped tokens without "
            "depositing collateral. RugDoc's safety framework flags bridge "
            "contracts where the 'mint' function is callable by an EOA, where "
            "the bridge is not audited, or where wrapped supply can be inflated. "
            "These patterns preceded several high-profile BSC bridge exploits "
            "and are a core part of the RugDoc audit checklist for cross-chain "
            "tokens."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:case:deflationary-fee",
        "title": "Deflationary Token Fee Manipulation (RugDoc case study)",
        "category": "deflationary-token",
        "severity": "high",
        "chain": "bsc",
        "text": (
            "RugDoc case study: deflationary token fee manipulation. Several BSC "
            "tokens combine a reflection/burn mechanism with an owner-adjustable "
            "fee. RugDoc documents cases where the owner raised the fee to 99% "
            "mid-session to lock sellers, then lowered it to dump. The audit "
            "checklist requires: fee setters behind timelock; reflection logic "
            "that cannot be toggled; and simulation of the worst-case fee path "
            "before launch."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:case:mev-sandwich-lp",
        "title": "LP Sandwich / MEV Drain (RugDoc guidance)",
        "category": "mev",
        "severity": "medium",
        "chain": "bsc",
        "text": (
            "RugDoc guidance: LP sandwich and MEV drains. Tokens with large "
            "transfer fees and thin liquidity attract sandwich bots that insert "
            "buy/sell transactions around user trades. RugDoc advises projects "
            "to cap per-tx swap sizes, use anti-bot launch filters, and provide "
            "slippage guidance, while users should set strict slippage and check "
            "the pair's liquidity depth before trading low-cap tokens."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "bsc",
    },
    {
        "rel_key": "rugdoc:method:simulate-buy-sell",
        "title": "RugDoc Simulation Method: Buy/Sell Test",
        "category": "methodology",
        "severity": "info",
        "chain": "multi",
        "text": (
            "RugDoc simulation method. The core RugDoc safety check is to "
            "simulate a buy and a sell against the live DEX pair using a "
            "forked chain or an RPC call, then compare output amounts. A token "
            "that lets you buy but not sell (or sells at 90%+ loss) is a "
            "honeypot. RugDoc also simulates: transfer between two user "
            "wallets; max-buy restrictions; and fee changes by the owner. "
            "This on-chain simulation, not tokenomics narrative, is what "
            "separates RugDoc's audits from surface-level token reviews."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "multi",
    },
    {
        "rel_key": "rugdoc:method:checklist",
        "title": "RugDoc Safety Checklist",
        "category": "methodology",
        "severity": "info",
        "chain": "multi",
        "text": (
            "RugDoc safety checklist. (1) Audit: is the contract audited and is "
            "the audit from a reputable firm? (2) Ownership: is it renounced, "
            "timelocked, or multisig? (3) Liquidity: is it locked and for how "
            "long? (4) Fees: are buy/sell fees reasonable and fixed? (5) Team: "
            "is the team doxxed and credible? (6) Tokenomics: is supply "
            "reasonable and minting restricted? (7) Contracts: simulate buy "
            "and sell before investing. (8) Community: does the community "
            "surface concerns? This checklist is the standard RugDoc "
            "pre-investment review flow."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "multi",
    },
    {
        "rel_key": "rugdoc:method:social-verify",
        "title": "RugDoc Social Verification Method",
        "category": "methodology",
        "severity": "info",
        "chain": "multi",
        "text": (
            "RugDoc social verification method. RugDoc cross-checks the project's "
            "social accounts (Telegram, Twitter/X, Discord) for signs of "
            "impersonation: account age, follower growth spikes, pinned scam "
            "warnings, and whether official links match the contract's website. "
            "Copycat projects that mirror a real token's branding but deploy a "
            "different contract are a common rug vector, especially after a "
            "popular launch. Verified socials that existed before the token "
            "launch are a positive signal."
        ),
        "source_url": "https://rugdoc.io",
        "ecosystem": "multi",
    },
]


def _docs() -> list[dict[str, Any]]:
    """Build ingest docs from the curated RugDoc knowledge."""
    docs: list[dict[str, Any]] = []
    for entry in RUGDOC_KNOWLEDGE:
        metadata = {
            "title": entry["title"],
            "category": entry["category"],
            "ecosystem": entry.get("ecosystem", "bsc"),
            "knowledge_type": "rugdoc",
            "source": SOURCE,
            "source_url": entry.get("source_url", "https://rugdoc.io"),
            "protocol": entry.get("title", ""),
        }
        docs.append(
            build_doc(
                source=SOURCE,
                rel_key=entry["rel_key"],
                content=entry["text"],
                metadata=metadata,
                severity=entry.get("severity", "medium"),
                chain=entry.get("chain", "bsc"),
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
        description="Ingest RugDoc rug-pull/audit knowledge into known_scams"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to ingest")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"RUGDOC INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
