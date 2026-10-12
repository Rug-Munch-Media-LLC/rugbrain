#!/usr/bin/env python3
"""Ingest DeFiLlama hacks database into RugBrain hacks_v2_8b collection.

Source: https://api.llama.fi/hacks (maintained by the DeFiLlama team)
License: CC0 or similar (public domain)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

# Ensure src is on path for embedder
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.embedder import get_embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HACKS_API = "https://api.llama.fi/hacks"
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
COLLECTION = "hacks_v2_8b"
BATCH_SIZE = 100


def to_uuid(doc_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def fetch_hacks(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    resp = await client.get(HACKS_API, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def hack_to_text(item: dict[str, Any]) -> str:
    name = item.get("name", "").strip()
    amount = item.get("amount", 0)
    chains = item.get("chain", [])
    chain_str = ", ".join(chains) if isinstance(chains, list) else str(chains)
    classification = item.get("classification", "").strip()
    technique = item.get("technique", "").strip()
    target_type = item.get("targetType", "").strip()
    date_ts = item.get("date", 0)
    date_str = datetime.fromtimestamp(date_ts, tz=UTC).isoformat() if date_ts > 0 else ""
    return (
        f"DeFiLlama hack: {name}. Amount: ${amount:,.2f}. Chains: {chain_str}. "
        f"Classification: {classification}. Technique: {technique}. Target: {target_type}. Date: {date_str}."
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
    url = f"{QDRANT_URL}/collections/{COLLECTION}/points"
    resp = await client.put(url, json={"points": points})
    resp.raise_for_status()
    return len(points)


async def ingest_hacks(embedder, days: int | None = None, limit: int | None = None) -> int:
    async with httpx.AsyncClient(timeout=120.0) as client:
        hacks = await fetch_hacks(client)
        logger.info("Fetched %d hack records", len(hacks))

        if days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            hacks = [
                h
                for h in hacks
                if h.get("date", 0) >= int(cutoff.timestamp())
            ]
            logger.info("Filtered to %d hacks from last %d days", len(hacks), days)

        if limit:
            hacks = hacks[:limit]

        total = 0
        texts, payloads, ids = [], [], []
        for item in hacks:
            name = item.get("name", "").strip()
            if not name:
                continue
            text = hack_to_text(item)
            pid = to_uuid(f"defillama:hack:{name}")
            payload = {
                "source": "defillama",
                "type": "hack",
                "name": name,
                "amount": item.get("amount", 0),
                "chain": item.get("chain", []),
                "classification": item.get("classification", "").strip(),
                "technique": item.get("technique", "").strip(),
                "target_type": item.get("targetType", "").strip(),
                "date": item.get("date", 0),
                "date_iso": datetime.fromtimestamp(item.get("date", 0), tz=UTC).isoformat()
                if item.get("date", 0) > 0
                else "",
                "bridgeHack": item.get("bridgeHack", False),
                "returnedFunds": item.get("returnedFunds"),
                "defillamaId": item.get("defillamaId"),
                "language": item.get("language", ""),
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

        logger.info("Ingested %d hack points into %s", total, COLLECTION)
        return total


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest DeFiLlama hacks dataset")
    parser.add_argument("--days", type=int, default=None, help="Only ingest hacks from last N days")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of records")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print count only")
    args = parser.parse_args()

    embedder = get_embedder()
    logger.info("Using embedder: %s (%dd)", embedder.__class__.__name__, embedder.dim)

    if args.dry_run:
        async with httpx.AsyncClient(timeout=60.0) as client:
            hacks = await fetch_hacks(client)
            print(f"Hacks: {len(hacks)}")
            if args.days:
                cutoff = datetime.now(UTC) - timedelta(days=args.days)
                filtered = [
                    h
                    for h in hacks
                    if h.get("date", 0) >= int(cutoff.timestamp())
                ]
                print(f"Hacks from last {args.days} days: {len(filtered)}")
        return

    total = await ingest_hacks(embedder, args.days, args.limit)
    logger.info("Total points ingested: %d", total)


if __name__ == "__main__":
    asyncio.run(main())
