#!/usr/bin/env python3
"""Ingest Chainabuse scam-report knowledge into the ``crime_reports`` collection.

Source: Chainabuse (https://chainabuse.com) — a community scam-reporting
platform (TRM Labs) that aggregates reports of phishing, pig-butchering,
romance scams, fake exchanges, and other crypto fraud. The script ships a
curated dataset of the reporting taxonomy and representative scam-report
patterns so RugBrain can answer 'what scams have been reported and how do
I recognize them?'. When ``CHAINABUSE_API_KEY`` is set it can also fetch
live reports from the Chainabuse public API (fail-open).

Usage:
    python scripts/ingest_chainabuse.py --dry-run
    python scripts/ingest_chainabuse.py --limit 20
    python scripts/ingest_chainabuse.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at INFINITY_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
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
logger = logging.getLogger("ingest_chainabuse")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import build_doc, write_docs  # noqa: E402

COLLECTION = "crime_reports"
SOURCE = "chainabuse"

CHAINABUSE_API = os.getenv("CHAINABUSE_API_URL", "https://api.chainabuse.com").rstrip("/")
CHAINABUSE_API_KEY = os.getenv("CHAINABUSE_API_KEY", "")

# ── Curated Chainabuse report taxonomy ──────────────────────────────────
CHAINABUSE_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "rel_key": "chainabuse:taxonomy:phishing",
        "title": "Chainabuse Report Type: Phishing",
        "category": "phishing",
        "severity": "critical",
        "chain": "multi",
        "text": (
            "Chainabuse report type: phishing. Reports where attackers "
            "impersonate legitimate services (exchanges, wallets, airdrops, "
            "support) to steal credentials, seed phrases, or funds. Common "
            "vectors: fake websites, malicious browser extensions, "
            "lookalike domains, and social-media impersonation. Chainabuse "
            "collects the victim address, the reported scam address, the "
            "scam URL, and a description; analysts tag the report so "
            "platforms can block the addresses."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "chainabuse:taxonomy:investment-scam",
        "title": "Chainabuse Report Type: Investment Scam",
        "category": "investment-scam",
        "severity": "critical",
        "chain": "multi",
        "text": (
            "Chainabuse report type: investment scam. Fake trading platforms, "
            "fake yield programs, and pump-and-dump schemes that persuade "
            "victims to deposit funds with the promise of returns. Victims "
            "often cannot withdraw. Chainabuse tracks the fake platform "
            "domain, the deposit addresses, and the communication channel "
            "(Telegram, WhatsApp). RugBrain treats investment-scam addresses "
            "as high-severity crime-report hits."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "chainabuse:taxonomy:romance-scam",
        "title": "Chainabuse Report Type: Romance Scam",
        "category": "romance-scam",
        "severity": "critical",
        "chain": "multi",
        "text": (
            "Chainabuse report type: romance scam (pig butchering). Long-term "
            "social manipulation where the attacker builds a romantic "
            "relationship, then steers the victim to a fake investment "
            "platform. Victims deposit repeatedly before discovering the "
            "platform is fake. Chainabuse reports include the wallet "
            "addresses used for deposits and the associated social profiles. "
            "Pig-butchering is among the largest crypto fraud categories by "
            "victim losses."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "chainabuse:taxonomy:impersonation",
        "title": "Chainabuse Report Type: Impersonation / Giveaway Scam",
        "category": "impersonation",
        "severity": "high",
        "chain": "multi",
        "text": (
            "Chainabuse report type: impersonation / giveaway scam. Fake "
            "celebrity or brand accounts announce a giveaway and ask victims "
            "to send crypto to 'verify' or claim a prize. Chainabuse "
            "aggregates the fake account handles and the receiving "
            "addresses. These reports are time-sensitive: the same address "
            "is reused across many small deposits."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "chainabuse:taxonomy:fake-exchange",
        "title": "Chainabuse Report Type: Fake Exchange",
        "category": "fake-exchange",
        "severity": "critical",
        "chain": "multi",
        "text": (
            "Chainabuse report type: fake exchange. A lookalike exchange "
            "website that accepts deposits and blocks withdrawals. Victims "
            "typically find the site through ads or social media. The fake "
            "exchange may show fake balances and require 'taxes' or 'fees' "
            "to unlock withdrawals. Chainabuse catalogs the fraudulent "
            "domain, deposit addresses, and customer-support contacts used "
            "by the scammers."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "chainabuse:taxonomy:wallet-drainer",
        "title": "Chainabuse Report Type: Wallet Drainer",
        "category": "wallet-drainer",
        "severity": "critical",
        "chain": "multi",
        "text": (
            "Chainabuse report type: wallet drainer. Malicious contracts or "
            "dApps that obtain token approvals or seed phrases and drain "
            "wallets. On-chain the drainer deploys many addresses; the "
            "reported address is usually the initial sink. Chainabuse "
            "reports pair the drainer address with the phishing URL used to "
            "obtain the approval. RugBrain links these to Revoke.cash "
            "approval-exploit knowledge."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "chainabuse:method:report-flow",
        "title": "Chainabuse Report Flow and Data Shape",
        "category": "methodology",
        "severity": "info",
        "chain": "multi",
        "text": (
            "Chainabuse report flow and data shape. Reports are submitted by "
            "victims with: scam type, victim address, reported address, "
            "scam URL, date, and description. TRM analysts review and tag "
            "reports; published reports power blocklists and warnings in "
            "wallets and exchanges. For RugBrain the useful fields are the "
            "reported address, the scam category, and the date — these feed "
            "crime-report search and address-risk scoring."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "multi",
    },
    {
        "rel_key": "chainabuse:case:btc-phishing",
        "title": "Chainabuse Case Pattern: BTC Address Reuse Phishing",
        "category": "phishing",
        "severity": "high",
        "chain": "bitcoin",
        "text": (
            "Chainabuse case pattern: BTC address reuse phishing. Scammers "
            "publish a fake donation/verify address that receives many small "
            "BTC deposits. Because Bitcoin addresses are reused, the same "
            "address appears across many reports. RugBrain's address-risk "
            "scoring treats an address with multiple crime reports and "
            "inbound small-value deposits as a high-confidence scam sink."
        ),
        "source_url": "https://chainabuse.com",
        "ecosystem": "bitcoin",
    },
]


def _docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in CHAINABUSE_KNOWLEDGE:
        metadata = {
            "title": entry["title"],
            "category": entry["category"],
            "ecosystem": entry.get("ecosystem", "multi"),
            "knowledge_type": "chainabuse",
            "source": SOURCE,
            "source_url": entry.get("source_url", "https://chainabuse.com"),
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


async def _live_reports(client: httpx.AsyncClient, limit: int) -> list[dict[str, Any]]:
    """Fetch recent Chainabuse reports (requires API key, fail-open)."""
    if not CHAINABUSE_API_KEY:
        return []
    try:
        r = await client.get(
            f"{CHAINABUSE_API}/v1/reports",
            params={"limit": min(limit, 100)},
            headers={"Authorization": f"Bearer {CHAINABUSE_API_KEY}"},
            timeout=30.0,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        reports = data if isinstance(data, list) else data.get("reports", [])
        docs: list[dict[str, Any]] = []
        for rep in reports:
            text = (
                f"Chainabuse report: {rep.get('scam_type') or rep.get('category') or 'unknown'}.\n"
                f"Description: {rep.get('description', '')}\n"
                f"Scam URL: {rep.get('scam_url', '')}\n"
                f"Reported address: {rep.get('reported_address', '')}\n"
                f"Date: {rep.get('date', '')}"
            )
            metadata = {
                "title": f"Chainabuse report {rep.get('scam_type') or 'unknown'}",
                "category": rep.get("scam_type") or rep.get("category") or "unknown",
                "ecosystem": "multi",
                "knowledge_type": "chainabuse_live",
                "source": SOURCE,
                "source_url": "https://chainabuse.com",
                "protocol": rep.get("reported_address", ""),
                "address": rep.get("reported_address", ""),
                "attack_vector": rep.get("scam_type") or "unknown",
            }
            docs.append(
                build_doc(
                    source=SOURCE,
                    rel_key=f"chainabuse:live:{rep.get('id') or rep.get('reported_address')}",
                    content=text,
                    metadata=metadata,
                    severity="critical",
                    chain="multi",
                    collection=COLLECTION,
                )
            )
        return docs
    except Exception as e:
        logger.debug("chainabuse_live_fetch_failed err=%s", str(e)[:120])
        return []


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    docs = _docs()
    if args.limit:
        docs = docs[: args.limit]

    store = QdrantVectorStore()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            live = await _live_reports(client, args.limit or 20)
            logger.info("chainabuse_live_reports=%d", len(live))
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
        description="Ingest Chainabuse scam-report knowledge into crime_reports"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max curated entries")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"CHAINABUSE INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
