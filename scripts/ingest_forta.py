#!/usr/bin/env python3
"""Ingest Forta malicious smart contract dataset into ``scam_intel_v2_8b``.

Source: forta-network/labelled-datasets GitHub repo
  - labels/1/malicious_smart_contracts.csv (Ethereum Mainnet, chainId 1)
  - labels/10/malicious_smart_contracts.csv (Optimism, chainId 10)

CSV schema (per README):
  contract_address, contract_tag, contract_creator, contract_creation_tx,
  contract_creator_tag, source, notes, contract_creator_etherscan_label

Each contract is embedded (4096-dim Qwen3-Embedding-8B via DeepInfra) and upserted into
``scam_intel_v2_8b`` with canonical metadata (chain, attack_vector=contract_tag,
source, source_url, creator, creation_tx, notes).

Idempotent: point IDs are deterministic UUIDv5 from ``forta:{chain}:{address}``.

Usage:
    python scripts/ingest_forta.py --dry-run --limit 10
    python scripts/ingest_forta.py --limit 5000
    python scripts/ingest_forta.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - DEEPINFRA_API_KEY for the 8B lane
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import sys
from io import StringIO
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common import content_hash
from rugbrain.embedder import get_embedder

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_forta")

# GitHub raw URLs for the two chains
FORTA_URLS = {
    "ethereum": "https://raw.githubusercontent.com/forta-network/labelled-datasets/main/labels/1/malicious_smart_contracts.csv",
    "optimism": "https://raw.githubusercontent.com/forta-network/labelled-datasets/main/labels/10/malicious_smart_contracts.csv",
}
COLLECTION = "scam_intel_v2_8b"
SOURCE = "forta"
SOURCE_URL = "https://github.com/forta-network/labelled-datasets"
REQUEST_TIMEOUT = 60.0

# Map Forta tags to canonical attack vectors
_TAG_TO_VECTOR = {
    "exploit": "exploit",
    "heist": "exploit",
    "phish-hack": "phishing",
    "phishing": "phishing",
    "malware": "malware",
    "scam": "scam",
    "ponzi": "ponzi-scheme",
    "rugpull": "rug-pull",
    "honeypot": "honeypot",
}


async def _fetch_csv(client: httpx.AsyncClient, url: str) -> str:
    """Download CSV content."""
    resp = await client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _parse_csv(csv_text: str, chain: str) -> list[dict[str, Any]]:
    """Parse CSV into list of dicts with normalized fields."""
    reader = csv.DictReader(StringIO(csv_text))
    entries = []
    for row in reader:
        address = row.get("contract_address", "").strip().lower()
        if not address or not address.startswith("0x"):
            continue
        tag = row.get("contract_tag", "").strip().lower()
        creator = row.get("contract_creator", "").strip().lower()
        creation_tx = row.get("contract_creation_tx", "").strip()
        creator_tag = row.get("contract_creator_tag", "").strip()
        source = row.get("source", "").strip()
        notes = row.get("notes", "").strip()
        creator_label = row.get("contract_creator_etherscan_label", "").strip()

        vector = _TAG_TO_VECTOR.get(tag, "unknown")

        entries.append(
            {
                "address": address,
                "chain": chain,
                "tag": tag,
                "vector": vector,
                "creator": creator,
                "creation_tx": creation_tx,
                "creator_tag": creator_tag,
                "source": source,
                "notes": notes,
                "creator_label": creator_label,
            }
        )
    return entries


def _build_docs(entries: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """Build ingest docs from parsed entries."""
    docs = []
    for entry in entries[:limit] if limit else entries:
        address = entry["address"]
        chain = entry["chain"]
        tag = entry["tag"]
        vector = entry["vector"]

        rel_key = f"forta:{chain}:{address}"
        doc_id = f"{SOURCE}:{rel_key}"

        content = (
            f"Forta malicious contract: {address}\n"
            f"Chain: {chain}\n"
            f"Tag: {tag}\n"
            f"Attack vector: {vector}\n"
            f"Creator: {entry['creator']}\n"
            f"Creation tx: {entry['creation_tx']}\n"
            f"Creator tag: {entry['creator_tag']}\n"
            f"Source: {entry['source']}\n"
            f"Notes: {entry['notes']}\n"
            f"Creator Etherscan label: {entry['creator_label']}"
        )

        metadata = {
            "entity": address,
            "entity_type": "address",
            "chain": chain,
            "attack_vector": vector,
            "tag": tag,
            "creator": entry["creator"],
            "creation_tx": entry["creation_tx"],
            "creator_tag": entry["creator_tag"],
            "source": entry["source"],
            "notes": entry["notes"],
            "creator_label": entry["creator_label"],
            "source_type": "forta_malicious_contract",
            "source_url": SOURCE_URL,
        }

        # Determine severity based on tag
        severity = "critical" if tag in ("exploit", "heist") else "high"

        docs.append(
            {
                "doc_id": doc_id,
                "text": content,
                "metadata": metadata,
                "severity": severity,
                "chain": chain,
            }
        )
    return docs


async def run(dry_run: bool, limit: int | None) -> dict[str, Any]:
    stats = {
        "collection": COLLECTION,
        "source": SOURCE,
        "fetched": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        all_entries = []
        for chain, url in FORTA_URLS.items():
            try:
                csv_text = await _fetch_csv(client, url)
            except Exception as e:
                logger.warning(
                    "forta_fetch_failed chain=%s url=%s err=%s", chain, url, str(e)[:200]
                )
                stats["errors"] += 1
                continue
            entries = _parse_csv(csv_text, chain)
            logger.info("forta_fetched chain=%s entries=%d", chain, len(entries))
            all_entries.extend(entries)
            stats["fetched"] += len(entries)

        if not all_entries:
            logger.info("forta_no_entries")
            return stats

        docs = _build_docs(all_entries, limit)
        if not docs:
            logger.info("forta_no_docs_to_ingest")
            return stats

        texts = [d["text"] for d in docs]
        embedder = get_embedder()
        vectors = await embedder.embed(texts)
        for doc, vec in zip(docs, vectors, strict=False):
            if not vec:
                stats["errors"] += 1
                continue
            doc["vector"] = vec
            stats["embedded"] += 1

        if dry_run:
            logger.info("dry_run would_upsert=%d into=%s", stats["embedded"], COLLECTION)
            return stats

        store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
        for doc in docs:
            vec = doc.get("vector")
            if not vec:
                continue
            ok = await store.insert(
                doc_id=doc["doc_id"],
                collection=COLLECTION,
                embedding=vec,
                content=doc["text"],
                metadata=doc["metadata"],
                source=SOURCE,
                severity=doc["severity"],
                chain=doc["chain"],
            )
            if ok:
                stats["upserted"] += 1
            else:
                stats["errors"] += 1

    logger.info(
        "forta_done fetched=%d embedded=%d upserted=%d errors=%d dry_run=%s",
        stats["fetched"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Forta malicious contracts → scam_intel_v2_8b")
    parser.add_argument("--dry-run", action="store_true", help="fetch + embed but do not upsert")
    parser.add_argument("--limit", type=int, default=None, help="max contracts to ingest (cap)")
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit))
    import json

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
