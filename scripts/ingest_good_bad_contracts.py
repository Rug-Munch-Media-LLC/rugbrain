#!/usr/bin/env python3
"""Ingest the curated good-vs-bad contract dataset into the RugBrain knowledge graph.

Part 3 of the contract-knowledge expansion. Ingests:

  - GOOD_CONTRACTS  → ``solidity_patterns`` / ``solana_patterns`` with
    verdict=good (secure ERC20, ReentrancyGuard, UUPS proxy, timelock,
    multi-sig, pausable, RBAC, Anchor validation, safe close) and a condensed
    'best practice' doc into ``contract_security``.
  - BAD_CONTRACTS   → ``solidity_patterns`` / ``solana_patterns`` with
    verdict=bad (honeypots, unlimited mints, hidden backdoors, fake renounces,
    fee manipulation, blacklists, selfdestruct drains, wallet drainers, and
    Solana Token-2022 backdoors) and a condensed 'rug-pull indicator' doc
    into ``contract_security``.

Each snippet is embedded (2560-dim qwen3-embedding-4b) and upserted with canonical
metadata (verdict, ecosystem, red_flags/best_practices, source). Point IDs
are deterministic UUIDv5 from content hashes → idempotent re-runs.

Usage:
    python scripts/ingest_good_bad_contracts.py --dry-run
    python scripts/ingest_good_bad_contracts.py

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
logger = logging.getLogger("ingest_good_bad")

# scripts/ dir on sys.path so sibling data modules import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from good_bad_contracts_data import BAD_CONTRACTS, GOOD_CONTRACTS  # noqa: E402
from ingest_common import content_hash, embed_many  # noqa: E402

SOLIDITY_COLLECTION = "solidity_patterns"
SOLANA_COLLECTION = "solana_patterns"
SECURITY_COLLECTION = "contract_security"
SOURCE = "good_bad_contracts"


def _collection_for(language: str) -> str:
    return SOLANA_COLLECTION if language == "rust" else SOLIDITY_COLLECTION


def _contract_doc(contract: dict[str, Any]) -> tuple[str, str, dict[str, Any], str, str]:
    """Build (doc_id, content, metadata, severity, collection) for one snippet."""
    cid = contract["contract_id"]
    ecosystem = contract.get("ecosystem", "ethereum")
    language = contract.get("language", "solidity")
    verdict = contract.get("verdict", "bad")
    severity = "info" if verdict == "good" else "critical"
    signals = contract.get("best_practices") or contract.get("red_flags") or []
    content = (
        f"{contract['title']} — {ecosystem} {verdict} contract reference.\n"
        f"Code:\n{contract.get('code', '')}\n"
        f"Description: {contract.get('description', '')}\n"
        + (f"{'Best practices' if verdict == 'good' else 'Red flags'}: {'; '.join(signals)}")
    )
    metadata = {
        "contract_id": cid,
        "title": contract["title"],
        "ecosystem": ecosystem,
        "language": language,
        "verdict": verdict,
        "knowledge_type": "contract_reference",
        "best_practices": contract.get("best_practices", []),
        "red_flags": contract.get("red_flags", []),
        "chain": ecosystem,
    }
    collection = _collection_for(language)
    return f"gb:{cid}:{content_hash(content)}", content, metadata, severity, collection


def _indicator_doc(contract: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    """Build a condensed 'what to look for' security doc for a bad contract."""
    cid = contract["contract_id"]
    verdict = contract.get("verdict", "bad")
    severity = "info" if verdict == "good" else "critical"
    signals = contract.get("best_practices") or contract.get("red_flags") or []
    content = (
        f"{'Secure implementation guideline' if verdict == 'good' else 'Rug-pull indicator'}: "
        f"{contract['title']} ({contract.get('ecosystem', 'ethereum')}).\n"
        f"{contract.get('description', '')}\n"
        f"{'Best practices' if verdict == 'good' else 'Red flags'}: {'; '.join(signals)}"
    )
    metadata = {
        "contract_id": cid,
        "title": contract["title"],
        "ecosystem": contract.get("ecosystem", "ethereum"),
        "verdict": verdict,
        "category": "rug-pull" if verdict == "bad" else "best-practice",
        "knowledge_type": "contract_indicator",
        "best_practices": contract.get("best_practices", []),
        "red_flags": contract.get("red_flags", []),
        "chain": contract.get("ecosystem", "ethereum"),
    }
    return f"gb-ind:{cid}:{content_hash(content)}", content, metadata, severity


async def _ingest(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    docs: list[tuple[str, str, dict[str, Any], str, str | None]],
    dry_run: bool,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    if dry_run:
        for _, _, _, _, collection in docs:
            if collection:
                counts[collection] = counts.get(collection, 0) + 1
        logger.info("[DRY] would ingest: %s", counts)
        return counts

    for i in range(0, len(docs), 8):
        batch = docs[i : i + 8]
        vectors = await embed_many(client, [d[1] for d in batch])
        for (doc_id, content, metadata, severity, collection), vector in zip(
            batch, vectors, strict=False
        ):
            if not vector or not collection:
                continue
            ok = await store.insert(
                doc_id=doc_id,
                collection=collection,
                embedding=vector,
                content=content,
                metadata=metadata,
                source=SOURCE,
                severity=severity,
                chain=str(metadata.get("chain", "")),
            )
            if ok:
                counts[collection] = counts.get(collection, 0) + 1
    logger.info("ingested: %s", counts)
    return counts


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    docs: list[tuple[str, str, dict[str, Any], str, str | None]] = []
    for c in GOOD_CONTRACTS + BAD_CONTRACTS:
        docs.append((*_contract_doc(c),))
        # Condensed indicator docs always land in contract_security.
        ind_id, ind_content, ind_meta, ind_sev = _indicator_doc(c)
        docs.append((ind_id, ind_content, ind_meta, ind_sev, SECURITY_COLLECTION))

    async with httpx.AsyncClient(timeout=120.0) as client:
        store = QdrantVectorStore()
        try:
            return await _ingest(client, store, docs, args.dry_run)
        finally:
            await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest curated good/bad contract snippets into solidity_patterns / solana_patterns / contract_security"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    args = parser.parse_args()

    totals = asyncio.run(main_async(args))
    print("=" * 60)
    print("GOOD/BAD CONTRACT INGEST COMPLETE")
    for col, n in totals.items():
        print(f"  {col}: {n}")
    print("=" * 60)


if __name__ == "__main__":
    main()
