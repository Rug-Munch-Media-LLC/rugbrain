#!/usr/bin/env python3
"""Ingest the curated cryptography knowledge base into a Qdrant ``cryptography`` collection.

Part 2 of the RugBrain knowledge expansion. Builds the ``cryptography``
collection from ``cryptography_knowledge_data.py``, covering:

  * Cryptographic standards     — ECDSA, Ed25519, BLS, Schnorr, secp256k1,
                                   zk-SNARKs, zk-STARKs
  * Key management              — MPC, Shamir's Secret Sharing, hardware
                                   wallets, HD wallets, BIP-39
  * Hashing                     — keccak256, sha256, pedersen, poseidon, mimc
  * Commitment schemes          — Merkle trees, Verkle trees, KZG,
                                   hash commit-reveal
  * Zero-knowledge              — circuits, Groth16, Plonk, Halo2, Nova,
                                   Bulletproofs, Circom
  * Post-quantum                — lattice-based (Kyber/Dilithium),
                                   hash-based (XMSS/LMS/SPHINCS+), Shor

Each entry is embedded (1024-dim bge-m3) and upserted with canonical
metadata (category, knowledge_type, source, references). Point IDs are
deterministic UUIDv5 from content hashes → idempotent re-runs.

Usage:
    python scripts/ingest_cryptography.py --dry-run
    python scripts/ingest_cryptography.py --limit 10
    python scripts/ingest_cryptography.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any

import httpx

from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_cryptography")

# scripts/ dir on sys.path so sibling data modules import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in os.sys.path:
    os.sys.path.insert(0, sys_path_scripts)

from cryptography_knowledge_data import CRYPTOGRAPHY_KNOWLEDGE  # noqa: E402
from ingest_common import content_hash, embed_many  # noqa: E402

COLLECTION = "cryptography"
SOURCE = "cryptography_knowledge"


def _doc(entry: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    """Build (doc_id, content, metadata, severity) for one knowledge entry."""
    rel_key = entry["rel_key"]
    category = entry.get("category", "general")
    title = entry["title"]
    refs = entry.get("references", [])
    content = f"{title} — {category}.\nCategory: {category}.\n{entry.get('text', '')}" + (
        f"\nReferences: {'; '.join(refs)}" if refs else ""
    )
    metadata = {
        "title": title,
        "category": category,
        "knowledge_type": "cryptography",
        "source": SOURCE,
        "references": refs,
        "source_url": refs[0] if refs else "",
        "chain": "multi",
    }
    severity = "info"
    return f"crypto:{rel_key}:{content_hash(content)}", content, metadata, severity


async def _ingest(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    docs: list[tuple[str, str, dict[str, Any], str]],
    dry_run: bool,
) -> int:
    if dry_run:
        logger.info("[DRY RUN] would ingest %d points into %s", len(docs), COLLECTION)
        return len(docs)
    inserted = 0
    for i in range(0, len(docs), 8):
        batch = docs[i : i + 8]
        texts = [d[1] for d in batch]
        vectors = await embed_many(client, texts)
        for (doc_id, content, metadata, severity), vector in zip(batch, vectors, strict=False):
            if not vector:
                logger.warning("embed_empty doc=%s", doc_id[:40])
                continue
            ok = await store.insert(
                doc_id=doc_id,
                collection=COLLECTION,
                embedding=vector,
                content=content,
                metadata=metadata,
                source=SOURCE,
                severity=severity,
                chain="multi",
            )
            if ok:
                inserted += 1
    logger.info("collection=%s inserted=%d", COLLECTION, inserted)
    return inserted


async def main_async(args: argparse.Namespace) -> int:
    docs = [_doc(e) for e in CRYPTOGRAPHY_KNOWLEDGE]
    if args.limit:
        docs = docs[: args.limit]

    async with httpx.AsyncClient(timeout=120.0) as client:
        store = QdrantVectorStore()
        try:
            inserted = await _ingest(client, store, docs, args.dry_run)
        finally:
            await store.close()
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest curated cryptography knowledge into the cryptography collection"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to ingest")
    args = parser.parse_args()

    inserted = asyncio.run(main_async(args))
    print("=" * 60)
    print(f"CRYPTOGRAPHY INGEST COMPLETE: {inserted} points in {COLLECTION}")
    print("=" * 60)


if __name__ == "__main__":
    main()
