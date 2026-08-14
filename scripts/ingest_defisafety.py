#!/usr/bin/env python3
"""Ingest DeFi Safety protocol security ratings into ``contract_audits``.

Source: DeFi Safety (https://defisafety.com) — a protocol security rating
platform that scores DeFi projects on a 0-100 scale across audit quality,
team transparency, operational security, and economic design. This script
ships a curated dataset of the scoring framework and representative
ratings so RugBrain can reason about protocol security posture.

Usage:
    python scripts/ingest_defisafety.py --dry-run
    python scripts/ingest_defisafety.py --limit 20
    python scripts/ingest_defisafety.py

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
logger = logging.getLogger("ingest_defisafety")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import build_doc, write_docs  # noqa: E402

COLLECTION = "contract_audits"
SOURCE = "defisafety"

# ── Curated DeFi Safety knowledge ───────────────────────────────────────
DEFISAFETY_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "rel_key": "defisafety:framework:overview",
        "title": "DeFi Safety Rating Framework Overview",
        "category": "security-rating",
        "severity": "info",
        "chain": "multi",
        "text": (
            "DeFi Safety rating framework. Protocols are scored 0-100 on "
            "security posture using a weighted rubric: (1) Audit quality — do "
            "audits exist, from which firms, and do they cover the deployed "
            "code? (2) Team transparency — are developers identified and "
            "accountable? (3) Operational security — keys, multi-sig, incident "
            "response, insurance. (4) Economic design — does the tokenomics "
            "create systemic risk? Scores are published as a 'security score' "
            "per protocol and are a recognized independent benchmark in the "
            "DeFi ecosystem. A score below ~50 indicates serious gaps."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "defisafety:framework:audit-quality",
        "title": "DeFi Safety Criteria: Audit Quality",
        "category": "security-rating",
        "severity": "info",
        "chain": "multi",
        "text": (
            "DeFi Safety criteria: audit quality. Evaluates whether the "
            "protocol's smart contracts were audited, whether the audit firms "
            "are reputable, whether audits cover the actual deployed code and "
            "the current version, and whether findings were remediated. "
            "Re-audits after upgrades are weighted positively. Audit reports "
            "that are dated, cover only marketing materials, or come from "
            "unknown firms reduce the score. High-scoring protocols publish "
            "complete, current audit reports with remediation proofs."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "defisafety:framework:team-transparency",
        "title": "DeFi Safety Criteria: Team Transparency",
        "category": "security-rating",
        "severity": "info",
        "chain": "multi",
        "text": (
            "DeFi Safety criteria: team transparency. Anonymous teams with "
            "irreversible control (owner powers, upgrade rights) are scored "
            "lower. Transparent teams that are identifiable, publish regular "
            "updates, and document decision-making score higher. DeFi Safety "
            "treats 'anonymous team + admin keys' as one of the highest-risk "
            "combinations because accountability is absent."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "defisafety:framework:ops-security",
        "title": "DeFi Safety Criteria: Operational Security",
        "category": "security-rating",
        "severity": "info",
        "chain": "multi",
        "text": (
            "DeFi Safety criteria: operational security. Examines how the "
            "protocol protects its own operations: multi-sig requirements for "
            "admin actions, timelocks on upgrades, bug-bounty programs, "
            "insurance coverage, and incident-response playbooks. Protocols "
            "that can be drained by a single compromised key score poorly. "
            "Positive signals: 2-of-3 or higher multi-sig, 24-72h timelocks, "
            "active bug bounty, and a published response plan."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "defisafety:framework:economic-design",
        "title": "DeFi Safety Criteria: Economic Design",
        "category": "security-rating",
        "severity": "info",
        "chain": "multi",
        "text": (
            "DeFi Safety criteria: economic design. Reviews whether the "
            "protocol's incentives create systemic fragility: oracle "
            "dependency, concentrated liquidity, unbacked minting, or "
            "governance that can extract user funds. Protocols whose TVL "
            "depends on a single price source or a single liquidity pool "
            "score lower. The economic design axis predicts vulnerability "
            "classes (oracle manipulation, bank runs) rather than code bugs."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "defisafety:rating:aave",
        "title": "DeFi Safety Profile: Aave",
        "category": "security-rating",
        "severity": "info",
        "chain": "ethereum",
        "text": (
            "DeFi Safety profile: Aave. Long-running lending protocol with a "
            "strong security posture: multiple audits by leading firms across "
            "major versions, a long-lived public multi-sig governance, "
            "timelock-controlled upgrades, a large bug bounty, and extensive "
            "incident history (e.g. the 2022 Aave v2 BUSD incident and 2023 "
            "v2 freezing) handled without user fund loss. Aave consistently "
            "scores at the top of DeFi Safety's security ranking."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "defisafety:rating:uniswap",
        "title": "DeFi Safety Profile: Uniswap",
        "category": "security-rating",
        "severity": "info",
        "chain": "ethereum",
        "text": (
            "DeFi Safety profile: Uniswap. DEX with a minimal, battle-tested "
            "contract surface. v2/v3 received audits from top firms, and the "
            "core contracts are immutable (no admin keys on the canonical "
            "router/pair). Governance is decentralized and slow. Uniswap "
            "scores highly on audit quality and operational security; the "
            "primary residual risk is economic (concentrated liquidity, "
            "periphery integrations)."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "defisafety:rating:curve",
        "title": "DeFi Safety Profile: Curve",
        "category": "security-rating",
        "severity": "info",
        "chain": "ethereum",
        "text": (
            "DeFi Safety profile: Curve. The 2023 Curve/vyper reentrancy "
            "exploit (~$61M across pools) demonstrated the economic-design "
            "risk of a single shared codebase. Curve's own contracts are "
            "long-audited and minimally governed, but the ecosystem of "
            "derivative pools and gauge mechanisms creates systemic exposure. "
            "DeFi Safety profiles Curve as high audit quality with "
            "concentration risk across the ecosystem."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "ethereum",
    },
    {
        "rel_key": "defisafety:rating:small-anon",
        "title": "DeFi Safety Profile: Small Anonymous Projects",
        "category": "security-rating",
        "severity": "high",
        "chain": "multi",
        "text": (
            "DeFi Safety profile: small anonymous projects. Protocols with an "
            "anonymous team, no audit, admin keys held by an EOA, and no bug "
            "bounty score in the bottom decile. DeFi Safety's rubric makes "
            "clear that anonymous + unaudited + centralized control is the "
            "canonical high-risk profile, regardless of TVL or marketing. "
            "RugBrain uses this profile to seed risk scoring for new tokens."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "defisafety:method:use-in-risk",
        "title": "Using DeFi Safety Scores in RugBrain Risk Scoring",
        "category": "methodology",
        "severity": "info",
        "chain": "multi",
        "text": (
            "Using DeFi Safety scores in RugBrain risk scoring. A protocol "
            "with a DeFi Safety score < 50 receives a base risk uplift, while "
            "scores >= 80 receive a base risk reduction. The audit-quality "
            "subscore is used to weigh contract-analysis findings: unaudited "
            "contracts get higher severity for the same vulnerability class. "
            "DeFi Safety scores are a static layer combined with live scanner "
            "signals (honeypot, LP lock, ownership) at query time."
        ),
        "source_url": "https://defisafety.com",
        "ecosystem": "multi",
    },
]


def _docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in DEFISAFETY_KNOWLEDGE:
        metadata = {
            "title": entry["title"],
            "category": entry["category"],
            "ecosystem": entry.get("ecosystem", "multi"),
            "knowledge_type": "defisafety",
            "source": SOURCE,
            "source_url": entry.get("source_url", "https://defisafety.com"),
            "protocol": entry.get("title", ""),
        }
        docs.append(
            build_doc(
                source=SOURCE,
                rel_key=entry["rel_key"],
                content=entry["text"],
                metadata=metadata,
                severity=entry.get("severity", "info"),
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
        description="Ingest DeFi Safety protocol security ratings into contract_audits"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to ingest")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"DEFI SAFETY INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
