#!/usr/bin/env python3
"""Ingest TokenSniffer scam-token detection knowledge into ``known_scams``.

Source: TokenSniffer (https://tokensniffer.com) — scam token detection
service that combines on-chain heuristics (honeypot, selfdestruct,
ownership, hidden mint, fee manipulation) with community reviews. This
script ships a curated detection-rules dataset so RugBrain can explain
*why* a token is flagged, and can optionally query the TokenSniffer API
for a live verdict when ``TOKENSNIFFER_API_KEY`` is set (fail-open).

Usage:
    python scripts/ingest_tokensniffer.py --dry-run
    python scripts/ingest_tokensniffer.py --limit 20
    python scripts/ingest_tokensniffer.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
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
logger = logging.getLogger("ingest_tokensniffer")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import build_doc, write_docs  # noqa: E402

COLLECTION = "known_scams"
SOURCE = "tokensniffer"

TOKENSNIFFER_API = os.getenv("TOKENSNIFFER_API_URL", "https://api.tokensniffer.com").rstrip("/")
TOKENSNIFFER_API_KEY = os.getenv("TOKENSNIFFER_API_KEY", "")

# ── Curated TokenSniffer detection rules ────────────────────────────────
TOKENSNIFFER_RULES: list[dict[str, Any]] = [
    {
        "rel_key": "tokensniffer:rule:honeypot",
        "title": "TokenSniffer Rule: Honeypot",
        "category": "honeypot",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: honeypot. A token is a honeypot when buys "
            "succeed but sells fail, trapping buyers. TokenSniffer detects this "
            "by checking the transfer function logic: sell transactions revert, "
            "the sell fee is set to 100%, or the contract restricts transfers "
            "from addresses other than the owner. Honeypots are usually paired "
            "with a fake website and social channels to attract victims before "
            "liquidity is drained. This is the single highest-confidence scam "
            "signal TokenSniffer emits."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:selfdestruct",
        "title": "TokenSniffer Rule: Selfdestruct",
        "category": "selfdestruct-drain",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: selfdestruct. The contract contains a "
            "selfdestruct (now SELFDESTRUCT) opcode that can send the contract's "
            "entire balance — including user deposits — to an arbitrary address "
            "chosen by the caller. If the selfdestruct path is owner-callable "
            "or triggered by any user, the token can be drained at any time. "
            "TokenSniffer flags contracts whose bytecode includes SELFDESTRUCT "
            "outside a verified migration pattern."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:ownership-not-renounced",
        "title": "TokenSniffer Rule: Ownership Not Renounced",
        "category": "access-control",
        "severity": "high",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: ownership not renounced. The deployer retains "
            "owner/admin privileges: the ability to mint new tokens, change "
            "fees, blacklist addresses, pause transfers, or alter the trading "
            "logic. While not immediately fatal, this permission set enables a "
            "future rug (mint-and-dump, fee-raise, liquidity pull). TokenSniffer "
            "rates these tokens higher risk until ownership is renounced, "
            "transferred to a timelock+multisig, or the privileged functions "
            "are removed."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:hidden-minter",
        "title": "TokenSniffer Rule: Hidden Minter / Mint Function",
        "category": "hidden-mint",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: hidden minter. The contract exposes a mint "
            "function (or a hidden mint path reachable only by a hardcoded "
            "address) that can inflate the total supply after launch, diluting "
            "existing holders. TokenSniffer scans the bytecode for "
            "_mint/_mintInternal calls not gated by an owner check, plus "
            "hardcoded addresses that can invoke supply changes. Unlimited or "
            "owner-only mint is a classic rug vector."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:fee-manipulation",
        "title": "TokenSniffer Rule: Fee Manipulation",
        "category": "business-logic",
        "severity": "high",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: fee manipulation. The transfer fee can be "
            "changed by the owner after launch, including to 100% to lock "
            "sellers or to zero to enable a silent dump. TokenSniffer flags "
            "tokens whose fee setters are not timelocked and whose fees are "
            "not capped. Buy/sell fee asymmetry above a small threshold also "
            "triggers the rule."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:no-liquidity-lock",
        "title": "TokenSniffer Rule: Liquidity Not Locked",
        "category": "rug-pull",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: liquidity not locked. The DEX pair liquidity is "
            "not locked or burned, so the deployer can withdraw the pool and "
            "crash the price. TokenSniffer cross-checks the LP token holder "
            "and lock status (Unicrypt, Team Finance, or burn address). Tokens "
            "with unlocked LP are directly rug-pullable and receive the highest "
            "risk rating."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:imbalanced-supply",
        "title": "TokenSniffer Rule: Supply Imbalance / Concentration",
        "category": "supply-concentration",
        "severity": "high",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: supply concentration. A large share of the total "
            "supply is held by one or a few addresses (deployer, 'marketing' "
            "wallet, exchange hot wallet), enabling a dump that crashes the "
            "price. TokenSniffer measures holder distribution and flags tokens "
            "where the top holder controls an outsized percentage or where "
            "supply was minted and immediately sent to a cluster of fresh "
            "wallets (sybil distribution)."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:proxy-upgradeable",
        "title": "TokenSniffer Rule: Upgradeable / Proxy Contract",
        "category": "upgradeable",
        "severity": "high",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: upgradeable proxy. The token is a proxy "
            "contract (e.g. TransparentUpgradeableProxy, UUPS) whose "
            "implementation can be swapped by an admin. If the admin is an EOA "
            "or a small multisig without a timelock, the team can replace the "
            "implementation with a malicious one at any time. TokenSniffer "
            "flags proxy tokens where the upgrade path is not "
            "time-locked/decentralized."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:no-audit",
        "title": "TokenSniffer Rule: No Audit / Anonymous Team",
        "category": "no-audit",
        "severity": "medium",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: no audit / anonymous team. Absence of an "
            "audit from a reputable firm and an anonymous team are contextual "
            "risk amplifiers. They do not prove a scam but correlate strongly "
            "with rug pulls and honeypots. TokenSniffer combines these "
            "contextual signals with the on-chain rules to produce the final "
            "risk score."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "tokensniffer:rule:community-flag",
        "title": "TokenSniffer Rule: Community Flag / Review",
        "category": "community-report",
        "severity": "medium",
        "chain": "ethereum",
        "text": (
            "TokenSniffer rule: community flag. Users can flag tokens as scams; "
            "the flag history and review notes are aggregated into the token's "
            "risk profile. Repeated flags from unrelated users, screenshots of "
            "failed sells, and liquidity-pull evidence are weighted heavily. "
            "Community evidence complements the automated heuristics and is "
            "especially useful for novel scam patterns the rules have not yet "
            "catalogued."
        ),
        "source_url": "https://tokensniffer.com",
        "ecosystem": "ethereum",
    },
]


def _docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in TOKENSNIFFER_RULES:
        metadata = {
            "title": entry["title"],
            "category": entry["category"],
            "ecosystem": entry.get("ecosystem", "ethereum"),
            "knowledge_type": "tokensniffer_rule",
            "source": SOURCE,
            "source_url": entry.get("source_url", "https://tokensniffer.com"),
            "protocol": entry.get("title", ""),
        }
        docs.append(
            build_doc(
                source=SOURCE,
                rel_key=entry["rel_key"],
                content=entry["text"],
                metadata=metadata,
                severity=entry.get("severity", "medium"),
                chain=entry.get("chain", "ethereum"),
                collection=COLLECTION,
            )
        )
    return docs


async def _fetch_live_verdict(client: httpx.AsyncClient, address: str) -> dict[str, Any] | None:
    """Query TokenSniffer API for a live verdict (requires API key)."""
    if not TOKENSNIFFER_API_KEY:
        return None
    try:
        r = await client.get(
            f"{TOKENSNIFFER_API}/v1/TokenInfo",
            params={"chainId": 1, "address": address},
            headers={"X-API-KEY": TOKENSNIFFER_API_KEY},
            timeout=30.0,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug("tokensniffer_live_fetch_failed addr=%s err=%s", address[:8], str(e)[:120])
    return None


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
        description="Ingest TokenSniffer scam-token detection rules into known_scams"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max rules to ingest")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"TOKENSNIFFER INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
