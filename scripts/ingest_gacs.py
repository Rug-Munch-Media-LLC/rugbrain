#!/usr/bin/env python3
"""Ingest GACS Public Scam Dataset into RugBrain scam_patterns_8b collection.

Source: https://gacs.app/api/public/wallets.csv, /domains.csv, /scams.json
License: CC BY 4.0 (attribution required)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

# Ensure src is on path for embedder
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.embedder import get_embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GACS_BASE = "https://gacs.app/api/public"
WALLETS_CSV = f"{GACS_BASE}/wallets.csv"
DOMAINS_CSV = f"{GACS_BASE}/domains.csv"
SCAMS_JSON = f"{GACS_BASE}/scams.json"

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
COLLECTION = "scam_patterns_8b"
BATCH_SIZE = 100


def to_uuid(doc_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def fetch_wallets(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get(WALLETS_CSV, timeout=60.0)
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    reader = csv.DictReader(lines)
    return list(reader)


async def fetch_domains(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get(DOMAINS_CSV, timeout=60.0)
    resp.raise_for_status()
    lines = resp.text.strip().splitlines()
    reader = csv.DictReader(lines)
    return list(reader)


async def fetch_scams(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get(SCAMS_JSON, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def wallet_to_text(row: dict[str, Any]) -> str:
    """Convert wallet CSV row to embeddable text."""
    addr = row.get("wallet_address", "").strip()
    chain = row.get("chain", "").strip()
    category = row.get("category", "").strip()
    severity = row.get("severity", "").strip()
    reports = row.get("reports_count", "0")
    first_seen = row.get("first_seen", "")
    last_seen = row.get("last_seen", "")
    return (
        f"GACS scam wallet: {addr} on {chain}. Category: {category}. "
        f"Severity: {severity}. Reports: {reports}. First seen: {first_seen}. Last seen: {last_seen}."
    )


def domain_to_text(row: dict[str, Any]) -> str:
    domain = row.get("domain", "").strip()
    category = row.get("category", "").strip()
    severity = row.get("severity", "").strip()
    reports = row.get("reports_count", "0")
    first_seen = row.get("first_seen", "")
    last_seen = row.get("last_seen", "")
    return (
        f"GACS scam domain: {domain}. Category: {category}. "
        f"Severity: {severity}. Reports: {reports}. First seen: {first_seen}. Last seen: {last_seen}."
    )


def scam_to_text(item: dict[str, Any]) -> str:
    name = item.get("entity_name", "").strip()
    url = item.get("url", "").strip()
    category = item.get("category", "").strip()
    severity = item.get("severity", "").strip()
    reports = item.get("reports_count", 0)
    first_seen = item.get("first_seen", "")
    last_seen = item.get("last_seen", "")
    return (
        f"GACS scam entity: {name} ({url}). Category: {category}. "
        f"Severity: {severity}. Reports: {reports}. First seen: {first_seen}. Last seen: {last_seen}."
    )


async def upsert_batch(
    client: httpx.AsyncClient,
    embedder,
    texts: list[str],
    payloads: list[dict[str, Any]],
    ids: list[str],
) -> int:
    vectors = await embedder.embed(texts)
    points = []
    for i, (vec, payload, pid) in enumerate(zip(vectors, payloads, ids)):
        points.append({"id": pid, "vector": vec, "payload": payload})
    # Upsert via Qdrant REST API
    url = f"{QDRANT_URL}/collections/{COLLECTION}/points"
    resp = await client.put(url, json={"points": points})
    resp.raise_for_status()
    return len(points)


async def ingest_wallets(embedder, limit: int | None = None) -> int:
    async with httpx.AsyncClient(timeout=120.0) as client:
        wallets = await fetch_wallets(client)
        logger.info("Fetched %d wallet records", len(wallets))
        if limit:
            wallets = wallets[:limit]

        total = 0
        texts, payloads, ids = [], [], []
        for row in wallets:
            addr = row.get("wallet_address", "").strip()
            if not addr:
                continue
            text = wallet_to_text(row)
            pid = to_uuid(f"gacs:wallet:{addr}")
            payload = {
                "source": "gacs",
                "type": "wallet",
                "wallet_address": addr,
                "chain": row.get("chain", "").strip(),
                "category": row.get("category", "").strip(),
                "severity": row.get("severity", "").strip(),
                "reports_count": int(row.get("reports_count", 0)),
                "first_seen": row.get("first_seen", ""),
                "last_seen": row.get("last_seen", ""),
                "text": text,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
            texts.append(text)
            payloads.append(payload)
            ids.append(pid)

            if len(texts) >= BATCH_SIZE:
                await upsert_batch(client, embedder, texts, payloads, ids)
                total += len(texts)
                texts, payloads, ids = [], [], []

        if texts:
            await upsert_batch(client, embedder, texts, payloads, ids)
            total += len(texts)

        logger.info("Ingested %d wallet points into %s", total, COLLECTION)
        return total


async def ingest_domains(embedder, limit: int | None = None) -> int:
    async with httpx.AsyncClient(timeout=120.0) as client:
        domains = await fetch_domains(client)
        logger.info("Fetched %d domain records", len(domains))
        if limit:
            domains = domains[:limit]

        total = 0
        texts, payloads, ids = [], [], []
        for row in domains:
            domain = row.get("domain", "").strip()
            if not domain:
                continue
            text = domain_to_text(row)
            pid = to_uuid(f"gacs:domain:{domain}")
            payload = {
                "source": "gacs",
                "type": "domain",
                "domain": domain,
                "category": row.get("category", "").strip(),
                "severity": row.get("severity", "").strip(),
                "reports_count": int(row.get("reports_count", 0)),
                "first_seen": row.get("first_seen", ""),
                "last_seen": row.get("last_seen", ""),
                "text": text,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
            texts.append(text)
            payloads.append(payload)
            ids.append(pid)

            if len(texts) >= BATCH_SIZE:
                await upsert_batch(client, embedder, texts, payloads, ids)
                total += len(texts)
                texts, payloads, ids = [], [], []

        if texts:
            await upsert_batch(client, embedder, texts, payloads, ids)
            total += len(texts)

        logger.info("Ingested %d domain points into %s", total, COLLECTION)
        return total


async def ingest_scams(embedder, limit: int | None = None) -> int:
    async with httpx.AsyncClient(timeout=120.0) as client:
        scams = await fetch_scams(client)
        logger.info("Fetched %d scam JSON records", len(scams))
        if limit:
            scams = scams[:limit]

        total = 0
        texts, payloads, ids = [], [], []
        for item in scams:
            name = item.get("entity_name", "").strip()
            if not name:
                continue
            text = scam_to_text(item)
            pid = to_uuid(f"gacs:scam:{name}")
            payload = {
                "source": "gacs",
                "type": "scam",
                "entity_name": name,
                "url": item.get("url", "").strip(),
                "category": item.get("category", "").strip(),
                "severity": item.get("severity", "").strip(),
                "reports_count": item.get("reports_count", 0),
                "first_seen": item.get("first_seen", ""),
                "last_seen": item.get("last_seen", ""),
                "text": text,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
            texts.append(text)
            payloads.append(payload)
            ids.append(pid)

            if len(texts) >= BATCH_SIZE:
                await upsert_batch(client, embedder, texts, payloads, ids)
                total += len(texts)
                texts, payloads, ids = [], [], []

        if texts:
            await upsert_batch(client, embedder, texts, payloads, ids)
            total += len(texts)

        logger.info("Ingested %d scam points into %s", total, COLLECTION)
        return total


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest GACS scam dataset")
    parser.add_argument("--wallets", action="store_true", help="Ingest wallets CSV")
    parser.add_argument("--domains", action="store_true", help="Ingest domains CSV")
    parser.add_argument("--scams", action="store_true", help="Ingest scams JSON")
    parser.add_argument("--all", action="store_true", help="Ingest all sources")
    parser.add_argument("--limit", type=int, default=None, help="Limit records per source")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print counts only")
    args = parser.parse_args()

    if not any([args.wallets, args.domains, args.scams, args.all]):
        args.all = True

    embedder = get_embedder()
    logger.info("Using embedder: %s (%dd)", embedder.__class__.__name__, embedder.dim)

    if args.dry_run:
        async with httpx.AsyncClient(timeout=60.0) as client:
            if args.wallets or args.all:
                wallets = await fetch_wallets(client)
                print(f"Wallets: {len(wallets)}")
            if args.domains or args.all:
                domains = await fetch_domains(client)
                print(f"Domains: {len(domains)}")
            if args.scams or args.all:
                scams = await fetch_scams(client)
                print(f"Scams: {len(scams)}")
        return

    total = 0
    if args.wallets or args.all:
        total += await ingest_wallets(embedder, args.limit)
    if args.domains or args.all:
        total += await ingest_domains(embedder, args.limit)
    if args.scams or args.all:
        total += await ingest_scams(embedder, args.limit)

    logger.info("Total points ingested: %d", total)


if __name__ == "__main__":
    asyncio.run(main())
