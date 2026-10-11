#!/usr/bin/env python3
"""Ingest ScamSniffer phishing feeds into RugBrain collections.

Source: scamsniffer/scam-database GitHub repo (daily updated, 7-day delay)
  - blacklist/domains.json    — malicious Web3 phishing domains
  - blacklist/address.json    — malicious wallet addresses (ETH + others)
  - blacklist/combined.json   — mapping of domain → associated addresses

Domains → ``threat_intel_v2`` (live phased-intel collection).
Addresses → ``known_scams`` (documented scam registry).

Each entity is embedded (1024-dim bge-m3 via Infinity) and upserted with
canonical metadata (entity_type, entity, source, source_url, domain_date_archive
when available).

Idempotent: point IDs are deterministic from ``scamsniffer:{type}:{entity}``.

Usage:
    python scripts/ingest_scamsniffer.py --dry-run --limit 20
    python scripts/ingest_scamsniffer.py --limit 5000
    python scripts/ingest_scamsniffer.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common import embed_many

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_scamsniffer")

BASE_URL = "https://raw.githubusercontent.com/scamsniffer/scam-database/main/blacklist"
DOMAINS_URL = f"{BASE_URL}/domains.json"
ADDRESS_URL = f"{BASE_URL}/address.json"
SOURCE = "scamsniffer"
SOURCE_URL = "https://github.com/scamsniffer/scam-database"
DOMAIN_COLLECTION = "threat_intel_v2"
ADDRESS_COLLECTION = "known_scams"
EMBEDDING_DIM = 1024
REQUEST_TIMEOUT = 60.0


async def _fetch_json(client: httpx.AsyncClient, url: str) -> Any:
    resp = await client.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _domain_docs(domains: list[str], limit: int | None) -> list[dict[str, Any]]:
    docs = []
    for domain in domains[:limit] if limit else domains:
        domain = domain.strip().lower()
        if not domain:
            continue
        doc_id = f"{SOURCE}:domain:{domain}"
        content = f"ScamSniffer phishing domain: {domain}\nEntity: domain\nSource: {SOURCE_URL}"
        metadata = {
            "entity": domain,
            "entity_type": "domain",
            "attack_vector": "phishing",
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "source_type": "scamsniffer_domain",
        }
        docs.append({
            "doc_id": doc_id,
            "text": content,
            "metadata": metadata,
            "severity": "high",
            "chain": "multi",
            "collection": DOMAIN_COLLECTION,
        })
    return docs


def _address_docs(addresses: list[str], limit: int | None) -> list[dict[str, Any]]:
    docs = []
    for addr in addresses[:limit] if limit else addresses:
        addr = addr.strip().lower()
        if not addr:
            continue
        doc_id = f"{SOURCE}:address:{addr}"
        content = f"ScamSniffer phishing wallet: {addr}\nEntity: address\nSource: {SOURCE_URL}"
        metadata = {
            "entity": addr,
            "entity_type": "address",
            "attack_vector": "phishing",
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "source_type": "scamsniffer_address",
        }
        docs.append({
            "doc_id": doc_id,
            "text": content,
            "metadata": metadata,
            "severity": "high",
            "chain": "multi",
            "collection": ADDRESS_COLLECTION,
        })
    return docs


async def _upsert(client: httpx.AsyncClient, store: QdrantVectorStore, docs: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    failed = 0
    texts = [d["text"] for d in docs]
    vectors = await embed_many(client, texts)
    for doc, vec in zip(docs, vectors, strict=False):
        if not vec:
            failed += 1
            continue
        ok = await store.insert(
            doc_id=doc["doc_id"],
            collection=doc["collection"],
            embedding=vec,
            content=doc["text"],
            metadata=doc["metadata"],
            source=SOURCE,
            severity=doc["severity"],
            chain=doc["chain"],
        )
        if ok:
            inserted += 1
        else:
            failed += 1
    return {"inserted": inserted, "failed": failed}


async def run(dry_run: bool, limit: int | None) -> dict[str, Any]:
    stats = {
        "source": SOURCE,
        "domains_collection": DOMAIN_COLLECTION,
        "address_collection": ADDRESS_COLLECTION,
        "domains_fetched": 0,
        "addresses_fetched": 0,
        "domains_upserted": 0,
        "addresses_upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            domains = await _fetch_json(client, DOMAINS_URL)
            addresses = await _fetch_json(client, ADDRESS_URL)
        except Exception as e:
            logger.warning("scamsniffer_fetch_failed err=%s", str(e)[:200])
            stats["errors"] += 1
            return stats

        stats["domains_fetched"] = len(domains)
        stats["addresses_fetched"] = len(addresses)

        domain_docs = _domain_docs(domains, limit)
        address_docs = _address_docs(addresses, limit)

        if dry_run:
            stats["domains_upserted"] = len(domain_docs)
            stats["addresses_upserted"] = len(address_docs)
            logger.info(
                "scamsniffer_dry_run domains=%d addresses=%d",
                len(domain_docs),
                len(address_docs),
            )
            return stats

        store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://localhost:6333"))
        if domain_docs:
            res = await _upsert(client, store, domain_docs)
            stats["domains_upserted"] = res["inserted"]
            stats["errors"] += res["failed"]
        if address_docs:
            res = await _upsert(client, store, address_docs)
            stats["addresses_upserted"] = res["inserted"]
            stats["errors"] += res["failed"]

    logger.info(
        "scamsniffer_done domains_upserted=%d addresses_upserted=%d errors=%d",
        stats["domains_upserted"],
        stats["addresses_upserted"],
        stats["errors"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="ScamSniffer feeds → RugBrain")
    parser.add_argument("--dry-run", action="store_true", help="fetch + embed but do not upsert")
    parser.add_argument("--limit", type=int, default=None, help="max entities per feed (cap)")
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit))
    import json
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
