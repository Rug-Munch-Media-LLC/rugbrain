#!/usr/bin/env python3
"""Ingest Etherscan combined wallet labels into the RugBrain ``wallet_v2`` collection.

Source: ``rmi-backend/data/etherscan_combined_labels.csv`` — the curated
Etherscan label dataset (address, chain, source, name, label_type,
label_subtype, project, entity_type). 51k rows; ~47.5k have a real name.

Goal: give RugBrain wallet-labeling knowledge so the chatbot can answer
"what kind of address is 0x...?" and "is this wallet labeled malicious?".

Strategy:
  * Every security-relevant label (entity_type in
    malicious/exploiter/phishing/scam/hack/drainer/sandwich) is ingested —
    these power risk scoring and rug checks.
  * Well-known protocol labels (dex, bridge, exchange, stablecoin, lending,
    oracle, rollup, nft_marketplace, liquid_staking) are ingested up to a
    per-type cap so common entities are recognizable without flooding.
  * Deterministic UUIDv5 IDs from content → idempotent re-runs.

Collection ``wallet_v2`` uses named dense (1024-dim bge-m3) + ``bm25``
sparse vectors; ``QdrantVectorStore.insert`` handles both automatically.

Usage:
    python scripts/ingest_wallet_labels.py --dry-run
    python scripts/ingest_wallet_labels.py --limit 2000
    python scripts/ingest_wallet_labels.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_wallet_labels")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in os.sys.path:
    os.sys.path.insert(0, sys_path_scripts)

from ingest_common import content_hash, embed_many  # noqa: E402

COLLECTION = "wallet_v2"
SOURCE = "etherscan_combined_labels"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = REPO_ROOT.parent / "rmi-backend" / "data" / "etherscan_combined_labels.csv"

# Security-relevant entity types — always ingest (risk scoring depends on them).
SECURITY_ENTITY_TYPES = {
    "malicious",
    "exploiter",
    "phishing",
    "scam",
    "hack",
    "hacker",
    "drainer",
    "sandwich",
    "mixer",
    "tornado",
    "wallet_drainer",
}

# Well-known protocol entity types — ingest up to a cap each.
KNOWN_ENTITY_TYPES = {
    "dex": 400,
    "bridge": 300,
    "exchange": 300,
    "stablecoin": 200,
    "lending": 200,
    "oracle": 200,
    "rollup": 150,
    "nft_marketplace": 150,
    "liquid_staking": 150,
    "token": 100,
}

_SKIP_NAME_PARTS = ("null address", "unknown", "empty", "unlabeled")


def _is_security(entity_type: str) -> bool:
    return (entity_type or "").strip().lower() in SECURITY_ENTITY_TYPES


def _is_known(entity_type: str) -> bool:
    return (entity_type or "").strip().lower() in KNOWN_ENTITY_TYPES


def _meaningful(row: dict[str, str]) -> bool:
    name = (row.get("name") or "").strip()
    addr = (row.get("address") or "").strip().lower()
    if not addr or len(addr) < 10:
        return False
    lowered = name.lower()
    return bool(name) and not any(p in lowered for p in _SKIP_NAME_PARTS)


def load_labels(csv_path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Load labeled addresses from the CSV with security-first ordering."""
    if not csv_path.exists():
        logger.warning("csv_missing path=%s", csv_path)
        return []
    docs: list[dict[str, Any]] = []
    security: list[dict[str, Any]] = []
    known: list[dict[str, Any]] = []
    known_counts: dict[str, int] = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not _meaningful(row):
                continue
            addr = (row.get("address") or "").strip().lower()
            chain = (row.get("chain") or "ethereum").strip().lower()
            name = (row.get("name") or "").strip()
            entity_type = (row.get("entity_type") or "").strip().lower()
            project = (row.get("project") or "").strip()
            source = (row.get("source") or SOURCE).strip()

            doc = {
                "doc_id": f"wlabel:{chain}:{addr}:{content_hash(name)}",
                "content": f"Wallet {addr} on {chain} labeled: {name} (entity_type={entity_type or 'unknown'})",
                "metadata": {
                    "address": addr,
                    "chain": chain,
                    "label": name,
                    "entity_type": entity_type or "unknown",
                    "project": project,
                    "source": source,
                },
                "source": source,
                "severity": "high" if _is_security(entity_type) else "medium",
                "chain": chain,
            }
            if _is_security(entity_type):
                security.append(doc)
            elif _is_known(entity_type):
                known_counts[entity_type] = known_counts.get(entity_type, 0) + 1
                if known_counts[entity_type] <= KNOWN_ENTITY_TYPES[entity_type]:
                    known.append(doc)
            else:
                docs.append(doc)

    # Security first, then known protocols, then the rest (capped).
    docs = security + known + docs
    if limit:
        docs = docs[:limit]
    logger.info(
        "labels_loaded total=%d security=%d known=%d other=%d",
        len(docs),
        len(security),
        len(known),
        max(0, len(docs) - len(security) - len(known)),
    )
    return docs


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    csv_path = Path(args.csv) if args.csv else DEFAULT_CSV
    docs = load_labels(csv_path, limit=args.limit)
    if args.dry_run:
        print(f"[DRY RUN] would ingest {len(docs)} wallet labels into {COLLECTION}")
        return {"inserted": 0, "chunks": len(docs), "failed": 0}

    client = httpx.AsyncClient(timeout=900.0)
    store = QdrantVectorStore()
    stats = {"inserted": 0, "chunks": 0, "failed": 0}
    try:
        for i, doc in enumerate(docs, start=1):
            text = (doc.get("content") or "").strip()
            if not text:
                continue
            vectors = await embed_many(client, [text[:8000]])
            vector = vectors[0] if vectors else []
            if not vector:
                stats["failed"] += 1
                continue
            ok = await store.insert(
                doc_id=f"{doc['doc_id']}:{content_hash(text)}",
                collection=COLLECTION,
                embedding=vector,
                content=text,
                metadata=doc.get("metadata", {}),
                source=doc["source"],
                severity=doc.get("severity", "medium"),
                chain=doc.get("chain", ""),
            )
            if ok:
                stats["inserted"] += 1
            else:
                stats["failed"] += 1
            stats["chunks"] += 1
            if i % 200 == 0 or i == len(docs):
                logger.info(
                    "progress %d/%d, %d upserted (%d failed)",
                    i,
                    len(docs),
                    stats["inserted"],
                    stats["failed"],
                )
    finally:
        await client.aclose()
        await store.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Etherscan wallet labels into RAG")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max labels to ingest")
    parser.add_argument(
        "--csv",
        default=None,
        help="Path to etherscan_combined_labels.csv (default: rmi-backend/data)",
    )
    args = parser.parse_args()
    stats = asyncio.run(main_async(args))
    print(
        f"WALLET LABELS INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks ({stats['failed']} failed)"
    )


if __name__ == "__main__":
    main()
