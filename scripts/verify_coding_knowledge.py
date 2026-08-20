#!/usr/bin/env python3
"""Verify the RugBrain coding-knowledge expansion end-to-end.

Part 6 of the coding-knowledge expansion. Runs the acceptance queries against
the live ``coding_knowledge`` collection (plus the contract-pattern
collections) and reports PASS/FAIL:

  1. "how to implement reentrancy guard in Solidity" → OpenZeppelin
     ReentrancyGuard + CEI explanation.
  2. "Solana PDA security best practices" → Anchor docs + PDA security
     patterns (canonical bumps, seed validation).
  3. "Move vs Solidity resource model" → comparison of the linear resource
     model vs EVM accounting.
  4. "what is a flash loan attack pattern" → exploit knowledge (defi_exploits,
     scam_patterns) + coding guidance.

Usage:
    python scripts/verify_coding_knowledge.py
    python scripts/verify_coding_knowledge.py --collection coding_knowledge

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

from rugbrain.bge_embedders import InfinityBGEEmbedder
from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("verify_coding_knowledge")

DEFAULT_COLLECTIONS = (
    "coding_knowledge",
    "solidity_patterns",
    "solana_patterns",
    "contract_security",
    "audit_findings",
)

# (query, collections, pass-keywords, label)
QUERIES: list[tuple[str, tuple[str, ...], tuple[str, ...], str]] = [
    (
        "how to implement reentrancy guard in Solidity",
        DEFAULT_COLLECTIONS,
        ("reentrancyguard", "nonreentrant", "checks-effects", "cei", "openzeppelin"),
        "reentrancy guard implementation",
    ),
    (
        "Solana PDA security best practices",
        DEFAULT_COLLECTIONS,
        ("pda", "bump", "seed", "anchor", "derived"),
        "Solana PDA security",
    ),
    (
        "Move vs Solidity resource model",
        DEFAULT_COLLECTIONS,
        ("resource", "move", "copy", "drop", "linear"),
        "Move vs Solidity resource model",
    ),
    (
        "what is a flash loan attack pattern",
        ("defi_exploits", "scam_patterns", "contract_security", "coding_knowledge"),
        ("flash loan", "flashloan", "flash-loan", "borrow", "callback", "liquidity"),
        "flash loan attack pattern",
    ),
]


async def run_query(
    store: QdrantVectorStore,
    embedder: InfinityBGEEmbedder,
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
                min_similarity=0.35,
            )
        except Exception as e:
            logger.warning("search_failed collection=%s err=%s", collection, str(e)[:120])
            continue
        for h in hits:
            merged.append(
                {
                    "collection": collection,
                    "content": (h.get("content", "") or "")[:600],
                    "similarity": h.get("similarity", 0.0),
                    "source": h.get("source", ""),
                    "severity": h.get("severity", ""),
                }
            )
    merged.sort(key=lambda r: r.get("similarity", 0.0), reverse=True)
    return merged[:limit]


async def verify_queries(collections: tuple[str, ...]) -> list[dict[str, Any]]:
    store = QdrantVectorStore()
    embedder = InfinityBGEEmbedder()
    results: list[dict[str, Any]] = []
    try:
        for query, query_collections, keywords, label in QUERIES:
            effective = query_collections or collections
            hits = await run_query(store, embedder, query, effective)
            combined = " ".join(h.get("content", "") for h in hits).lower()
            matched = [kw for kw in keywords if kw.lower() in combined]
            passed = bool(matched)
            results.append(
                {
                    "query": query,
                    "label": label,
                    "collections": list(effective),
                    "passed": passed,
                    "matched_keywords": matched,
                    "top_hits": [
                        {
                            "collection": h["collection"],
                            "similarity": h["similarity"],
                            "content": h["content"][:220],
                        }
                        for h in hits[:3]
                    ],
                }
            )
    finally:
        await store.close()
    return results


async def main_async(args: argparse.Namespace) -> int:
    collections = tuple(args.collection.split(",")) if args.collection else DEFAULT_COLLECTIONS
    results = await verify_queries(collections)
    all_passed = True
    print("=" * 70)
    print("RUG BRAIN CODING-KNOWLEDGE VERIFICATION")
    print("=" * 70)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        all_passed = all_passed and r["passed"]
        print(f"\n[{status}] {r['label']}")
        print(f"  query: {r['query']}")
        print(f"  matched keywords: {', '.join(r['matched_keywords']) or 'NONE'}")
        for h in r["top_hits"]:
            print(f"    - [{h['collection']}] {h['similarity']:.3f}  {h['content'][:160]}")
    print("=" * 70)
    print(f"RESULT: {'ALL PASS' if all_passed else 'SOME FAILED'}")
    print("=" * 70)
    return 0 if all_passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify RugBrain coding-knowledge ingestion")
    parser.add_argument(
        "--collection",
        default=None,
        help="Comma-separated collections to verify (default: coding + contract pattern sets)",
    )
    args = parser.parse_args()

    sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
    if sys_path_scripts not in sys.path:
        sys.path.insert(0, sys_path_scripts)

    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
