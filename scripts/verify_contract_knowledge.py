#!/usr/bin/env python3
"""Verify the RugBrain contract-knowledge expansion end-to-end.

Part 5 of the contract-knowledge expansion. Runs the three acceptance
queries against the live knowledge collections and reports PASS/FAIL:

  1. "how to implement a secure ERC20 token"   → solidity_patterns +
     contract_security should return OpenZeppelin / secure ERC20 guidance.
  2. "common reentrancy vulnerabilities"        → contract_security should
     return reentrancy patterns.
  3. "Solana account validation best practices" → solana_patterns should
     return SPL/Anchor account-validation patterns.

Optional ``--analyze <address> [--chain <chain>]`` mode exercises the
Part 4 pipeline (source retrieval + static scan + knowledge compare) against
a live verified contract (e.g. a known token) and prints the assessment.

Usage:
    python scripts/verify_contract_knowledge.py
    python scripts/verify_contract_knowledge.py --analyze 0xdAC17F958D2ee523a2206206994597C13D831ec7 --chain ethereum

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://127.0.0.1:8004/v1) with qwen3-embedding-4b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("verify_contract_knowledge")

# (query, collections, pass-keywords, label)
QUERIES: list[tuple[str, tuple[str, ...], tuple[str, ...], str]] = [
    (
        "how to implement a secure ERC20 token",
        ("solidity_patterns", "contract_security"),
        ("erc20", "openzeppelin", "mint", "owner", "token"),
        "secure ERC20 guidance",
    ),
    (
        "common reentrancy vulnerabilities",
        ("contract_security",),
        ("reentrancy", "external call", "CEI", "nonReentrant", "guard"),
        "reentrancy patterns",
    ),
    (
        "Solana account validation best practices",
        ("solana_patterns",),
        ("signer", "account", "anchor", "seed", "bump", "validation", "program"),
        "Solana account validation",
    ),
]


async def run_query(
    store: QdrantVectorStore,
    embedder: Qwen3Embedder,
    query: str,
    collections: tuple[str, ...],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Embed *query* and search each collection, merging by similarity."""
    embedding = await embedder.embed_one(query)
    merged: list[dict[str, Any]] = []
    for collection in collections:
        try:
            hits = await store.search(
                embedding,
                collection=collection,
                limit=limit,
                min_similarity=0.4,
            )
        except Exception as e:
            logger.warning("search_failed collection=%s err=%s", collection, str(e)[:120])
            continue
        for h in hits:
            merged.append(
                {
                    "collection": collection,
                    "content": (h.get("content", "") or "")[:500],
                    "similarity": h.get("similarity", 0.0),
                    "source": h.get("source", ""),
                    "severity": h.get("severity", ""),
                }
            )
    merged.sort(key=lambda r: r.get("similarity", 0.0), reverse=True)
    return merged[:limit]


async def verify_queries() -> list[dict[str, Any]]:
    store = QdrantVectorStore()
    embedder = Qwen3Embedder()
    results: list[dict[str, Any]] = []
    try:
        for query, collections, keywords, label in QUERIES:
            hits = await run_query(store, embedder, query, collections)
            combined = " ".join(h.get("content", "") for h in hits).lower()
            matched = [kw for kw in keywords if kw.lower() in combined]
            passed = bool(matched)
            results.append(
                {
                    "query": query,
                    "label": label,
                    "collections": list(collections),
                    "passed": passed,
                    "matched_keywords": matched,
                    "top_hits": [
                        {
                            "collection": h["collection"],
                            "similarity": h["similarity"],
                            "content": h["content"][:200],
                        }
                        for h in hits[:3]
                    ],
                }
            )
    finally:
        await store.close()
    return results


async def analyze_address(address: str, chain: str) -> dict[str, Any]:
    from rugbrain.contract_analysis import analyze_contract_security

    result: dict[str, Any] = await analyze_contract_security(address, chain=chain, limit=5)
    return result


async def main_async(args: argparse.Namespace) -> int:
    if args.analyze:
        result = await analyze_address(args.analyze, args.chain)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("source_fetched") else 1

    results = await verify_queries()
    all_passed = True
    print("=" * 70)
    print("RUG BRAIN CONTRACT-KNOWLEDGE VERIFICATION")
    print("=" * 70)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        all_passed = all_passed and r["passed"]
        print(f"\n[{status}] {r['label']}")
        print(f"  query: {r['query']}")
        print(f"  collections: {', '.join(r['collections'])}")
        print(f"  matched keywords: {', '.join(r['matched_keywords']) or 'NONE'}")
        for h in r["top_hits"]:
            print(f"    - [{h['collection']}] {h['similarity']:.3f}  {h['content'][:150]}")
    print("=" * 70)
    print(f"RESULT: {'ALL PASS' if all_passed else 'SOME FAILED'}")
    print("=" * 70)
    return 0 if all_passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify RugBrain contract-knowledge ingestion")
    parser.add_argument("--analyze", default="", help="Run Part 4 pipeline on this address")
    parser.add_argument("--chain", default="ethereum", help="Chain for --analyze")
    args = parser.parse_args()

    # scripts/ dir on sys.path (not needed here, but keeps parity with ingesters).
    sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
    if sys_path_scripts not in sys.path:
        sys.path.insert(0, sys_path_scripts)

    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
