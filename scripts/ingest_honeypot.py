#!/usr/bin/env python3
"""Ingest Honeypot.is honeypot-detection knowledge into ``known_scams``.

Source: Honeypot.is (https://honeypot.is) — a honeypot detection API for
EVM tokens. The script ships a curated set of honeypot detection rules and
known honeypot case studies, and can additionally query the public
Honeypot.is API for a live verdict on addresses provided via ``--address``
or ``--address-file`` (fail-open when the API is unreachable).

Usage:
    python scripts/ingest_honeypot.py --dry-run
    python scripts/ingest_honeypot.py --limit 20
    python scripts/ingest_honeypot.py --address 0xabc... --address 0xdef...
    python scripts/ingest_honeypot.py --address-file addresses.txt

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_honeypot")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in os.sys.path:
    os.sys.path.insert(0, sys_path_scripts)

from ingest_common2 import build_doc, write_docs  # noqa: E402

COLLECTION = "known_scams"
SOURCE = "honeypot"

HONEYPOT_API = os.getenv("HONEYPOT_API_URL", "https://api.honeypot.is").rstrip("/")

# ── Curated Honeypot.is knowledge ───────────────────────────────────────
HONEYPOT_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "rel_key": "honeypot:rule:is-honeypot",
        "title": "Honeypot.is Core Rule: IsHoneypot",
        "category": "honeypot",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "Honeypot.is core rule: IsHoneypot. The API simulates a buy and a "
            "sell on the token's primary DEX pair and reports whether a "
            "honeypot exists. A honeypot is flagged when the sell simulation "
            "fails or returns a loss beyond the reported buy/sell fees, meaning "
            "buyers cannot exit. The verdict includes a simulation result, the "
            "simulated buy amount, the simulated sell amount, the buy/sell tax, "
            "and a list of detected issue categories. Honeypot.is is a primary "
            "source used by RugBrain's token scanners for quick honeypot "
            "screening."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
    {
        "rel_key": "honeypot:rule:simulation-window",
        "title": "Honeypot.is Simulation Window",
        "category": "honeypot",
        "severity": "info",
        "chain": "ethereum",
        "text": (
            "Honeypot.is simulation window. The API uses an in-memory EVM fork "
            "at a recent block to simulate trades without spending gas. The "
            "fork applies the token's current state, including balances and "
            "liquidity. Because the simulation uses the latest state, "
            "honeypots that only lock sellers in specific conditions (e.g. "
            "after a fee toggle) can evade a single snapshot; RugBrain "
            "re-scans tokens over time and correlates Honeypot.is verdicts "
            "with on-chain transfers."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
    {
        "rel_key": "honeypot:rule:fee-based",
        "title": "Honeypot.is Rule: Fee-Based Honeypot",
        "category": "honeypot",
        "severity": "high",
        "chain": "ethereum",
        "text": (
            "Honeypot.is rule: fee-based honeypot. A token that charges an "
            "extremely high sell fee (approaching 100%) effectively traps "
            "sellers without an explicit revert. Honeypot.is reports the "
            "buy tax and sell tax separately; when the sell tax leaves less "
            "than a small fraction of the invested value, the token is "
            "flagged. This pattern is common on BSC and newer EVM chains "
            "where token standards allow arbitrary transfer logic."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
    {
        "rel_key": "honeypot:rule:blacklist-honeypot",
        "title": "Honeypot.is Rule: Blacklist Honeypot",
        "category": "honeypot",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "Honeypot.is rule: blacklist honeypot. The token contract "
            "maintains a blacklist and blocks transfers from blacklisted "
            "addresses. Because the honeypot simulation uses a fresh address "
            "that is not blacklisted, the simulation may pass while real "
            "buyers with flagged wallets are trapped. RugBrain correlates "
            "this with deployer behavior: if the owner blacklists large "
            "holders after launch, the token is treated as a honeypot even "
            "when a clean-address simulation succeeds."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
    {
        "rel_key": "honeypot:case:novel-blacklist-drain",
        "title": "Honeypot.is Coverage: Blacklist-Then-Drain Case",
        "category": "honeypot",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "Honeypot.is coverage: blacklist-then-drain. A documented pattern "
            "is a token that looks tradable, then the owner blacklists "
            "non-whitelisted holders and transfers their balance to the "
            "deployer. Honeypot.is detects the blacklist mechanism in the "
            "contract and the swap simulation shows normal behavior for a "
            "fresh address; RugBrain's layered approach (Honeypot.is + "
            "contract analysis + transfer tracing) catches the drain. "
            "Detection heuristics: blacklist mapping exists; transfer() "
            "checks the mapping; owner can add entries; deployer receives "
            "large transfers after blacklist events."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
    {
        "rel_key": "honeypot:case:erc777-reentrancy",
        "title": "Honeypot.is Coverage: ERC777 Reentrancy Honeypot",
        "category": "honeypot",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "Honeypot.is coverage: ERC777 reentrancy honeypot. A small class "
            "of tokens uses ERC777 hooks to reenter the DEX pair during the "
            "sell, causing the sell simulation to fail or the pair to be "
            "drained. Honeypot.is flags tokens whose transfer path calls "
            "external hooks (tokensReceived/tokensToSend) that can reenter. "
            "The classic 2021 attack on imBTC/ERC777 reentrancy (Bancor) is "
            "the reference case."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
    {
        "rel_key": "honeypot:case:approval-drainer",
        "title": "Honeypot.is Coverage: Approval-Drainer Token",
        "category": "wallet-drainer",
        "severity": "critical",
        "chain": "ethereum",
        "text": (
            "Honeypot.is coverage: approval-drainer token. A token that "
            "silently requests unlimited token approval and then transfers "
            "approved balance to an attacker address. The swap simulation "
            "looks normal because the drain happens via transferFrom after "
            "the user approves. RugBrain treats approval-drainer tokens as a "
            "distinct scam family and recommends revoke.cash for cleanup. "
            "Honeypot.is verdicts are combined with contract-source analysis "
            "to catch these."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
    {
        "rel_key": "honeypot:method:interpret-verdict",
        "title": "Honeypot.is Interpretation Guide",
        "category": "methodology",
        "severity": "info",
        "chain": "evm",
        "text": (
            "Honeypot.is interpretation guide. Response fields: isHoneypot "
            "(bool verdict), simulationResult (buy/sell output), buyTax / "
            "sellTax (percent), detectedIssues (list). A verdict of "
            "isHoneypot=true is a strong scam signal. False negatives occur "
            "when the honeypot is condition-based (blacklist, fee toggle, "
            "whitelist) — so a clean verdict is not proof of safety. Always "
            "pair a Honeypot.is check with contract analysis, LP lock "
            "verification, and holder-distribution review."
        ),
        "source_url": "https://honeypot.is",
        "ecosystem": "evm",
    },
]


def _docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in HONEYPOT_KNOWLEDGE:
        metadata = {
            "title": entry["title"],
            "category": entry["category"],
            "ecosystem": entry.get("ecosystem", "evm"),
            "knowledge_type": "honeypot",
            "source": SOURCE,
            "source_url": entry.get("source_url", "https://honeypot.is"),
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


async def _live_verdict(client: httpx.AsyncClient, address: str) -> dict[str, Any] | None:
    """Query the public Honeypot.is API for one address (fail-open)."""
    try:
        r = await client.get(
            f"{HONEYPOT_API}/v2/IsHoneypot", params={"address": address}, timeout=30.0
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug("honeypot_live_fetch_failed addr=%s err=%s", address[:8], str(e)[:120])
    return None


async def _live_addresses(
    client: httpx.AsyncClient, addresses: list[str], dry_run: bool
) -> list[dict[str, Any]]:
    """Fetch live verdicts and build ingest docs for them."""
    docs: list[dict[str, Any]] = []
    for address in addresses:
        verdict = await _live_verdict(client, address)
        if verdict is None:
            continue
        honeypot = bool(verdict.get("isHoneypot"))
        sim = verdict.get("simulationResult") or {}
        issues = verdict.get("detectedIssues") or []
        text = (
            f"Honeypot.is live verdict for {address}.\n"
            f"isHoneypot: {honeypot}\n"
            f"Simulated buy: {sim.get('simulatedBuyAmount') or 'n/a'}\n"
            f"Simulated sell: {sim.get('simulatedSellAmount') or 'n/a'}\n"
            f"Buy tax: {verdict.get('buyTax')}% | Sell tax: {verdict.get('sellTax')}%\n"
            f"Detected issues: {', '.join(str(i) for i in issues)}\n"
            f"Contract address: {address}"
        )
        metadata = {
            "title": f"Honeypot.is verdict for {address[:10]}...",
            "category": "honeypot" if honeypot else "token-check",
            "ecosystem": "evm",
            "knowledge_type": "honeypot_live",
            "source": SOURCE,
            "source_url": f"https://honeypot.is/check?address={address}",
            "protocol": address,
            "is_honeypot": honeypot,
            "address": address,
        }
        docs.append(
            build_doc(
                source=SOURCE,
                rel_key=f"honeypot:live:{address}",
                content=text,
                metadata=metadata,
                severity="critical" if honeypot else "info",
                chain="ethereum",
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
            addresses = list(args.address or [])
            if args.address_file:
                path = Path(args.address_file)
                if path.exists():
                    addresses += [
                        line.strip()
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.startswith("#")
                    ]
            if addresses:
                live = await _live_addresses(client, addresses, args.dry_run)
                logger.info("honeypot_live_verdicts=%d", len(live))
                docs = docs + live
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
        description="Ingest Honeypot.is honeypot knowledge + live verdicts into known_scams"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max curated entries")
    parser.add_argument("--address", action="append", default=[], help="EVM address to check live")
    parser.add_argument("--address-file", default=None, help="File with addresses (one per line)")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"HONEYPOT INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
