#!/usr/bin/env python3
"""
Ingest free scam data sources into RAG known_scams collection.

Sources:
1. MetaMask eth-phishing-detect blacklist (~198K domains)
2. CryptoScamDB API (if available - currently 502)
3. ChainAbuse API (needs API key)

The MetaMask list is the largest accessible source of confirmed scam domains.
"""

import asyncio
import hashlib
import logging
import os
import sys
from typing import Any

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_free_scam_sources")

RAG_API = "http://localhost:8000/api/v1/rag/ingest"
COLLECTION = "known_scams"

METAMASK_LIST_URL = (
    "https://raw.githubusercontent.com/MetaMask/eth-phishing-detect/master/src/config.json"
)
CRYPTOSCAMDB_URLS = [
    "https://api.cryptoscamdb.org/v1/all",
    "https://api.cryptoscamdb.org/v1/scams",
]


def make_doc_id(content: str, source: str) -> str:
    return hashlib.sha256(f"known_scams:{content.lower()}:{source}".encode()).hexdigest()[:16]


async def ingest_via_api(content: str, metadata: dict[str, Any]) -> bool:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                RAG_API,
                json={
                    "collection": COLLECTION,
                    "content": content,
                    "metadata": metadata,
                },
            )
            return resp.status_code == 200
    except Exception as e:
        logger.debug(f"Ingest API error: {e}")
        return False


async def ingest_metamask_blacklist() -> tuple[int, int, bool]:
    """Ingest MetaMask phishing domain blacklist (~198K domains)."""
    logger.info("Fetching MetaMask phishing blacklist...")

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.get(METAMASK_LIST_URL)
            if resp.status_code != 200:
                logger.error(f"Failed to fetch MetaMask list: {resp.status_code}")
                return 0, 0, False
            data = resp.json()
        except Exception as e:
            logger.error(f"MetaMask list fetch error: {e}")
            return 0, 0, False

    blacklist = data.get("blacklist", [])
    logger.info(f"MetaMask blacklist: {len(blacklist)} entries")

    # Batch into groups by domain pattern for efficiency
    # Instead of 198K individual entries, group by TLD + category
    ingested = 0
    errors = 0

    # Group in batches of 50 domains per RAG doc
    batch_size = 50
    total_batches = (len(blacklist) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        batch = blacklist[batch_idx * batch_size : (batch_idx + 1) * batch_size]

        domain_list = "\n".join(batch)
        content = (
            f"MetaMask Confirmed Phishing/Scam Domains (Batch {batch_idx + 1}/{total_batches}):\n"
            f"{domain_list}\n"
            f"Source: MetaMask eth-phishing-detect project. These domains are confirmed "
            f"phishing, scam, or malicious websites targeting crypto users."
        )

        metadata = {
            "source": "metamask_phishing_detect",
            "batch_index": batch_idx,
            "total_batches": total_batches,
            "domain_count": len(batch),
            "category": "phishing_domain",
            "severity": "high",
            "doc_id": make_doc_id(f"metamask_batch_{batch_idx}", "metamask"),
        }

        if await ingest_via_api(content, metadata):
            ingested += 1
        else:
            errors += 1

        if (batch_idx + 1) % 100 == 0:
            logger.info(
                f"Progress: {batch_idx + 1}/{total_batches} batches | ingested: {ingested}, errors: {errors}"
            )
            await asyncio.sleep(0.05)

        # Rate limit: small delay every 20 batches
        if (batch_idx + 1) % 20 == 0:
            await asyncio.sleep(0.1)

    return ingested, errors, True


async def try_cryptoscamdb() -> list[dict[str, Any]] | None:
    """Try CryptoScamDB API if accessible."""
    for url in CRYPTOSCAMDB_URLS:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    scams = (
                        data
                        if isinstance(data, list)
                        else data.get("result", data.get("scams", []))
                    )
                    logger.info(f"CryptoScamDB returned {len(scams)} entries from {url}")
                    return scams
        except Exception as e:
            logger.debug(f"CryptoScamDB {url} failed: {e}")

    # mypy considers this unreachable (narrowing quirk across the try/except
    # loop); it is reachable when every URL fails.
    logger.warning("CryptoScamDB API unavailable (502)")
    return None


async def ingest_cryptoscamdb(scams: list[Any]) -> tuple[int, int]:
    """Ingest CryptoScamDB entries."""
    ingested = 0
    errors = 0

    for i, scam in enumerate(scams):
        if not isinstance(scam, dict):
            continue

        name = scam.get("name", "")
        url = scam.get("url", "")
        category = scam.get("category", "unknown")
        status = scam.get("status", "unknown")
        description = scam.get("description", "")
        addresses = scam.get("addresses", [])

        if addresses:
            for addr in addresses:
                if not addr or len(addr) < 10:
                    continue
                content = f"CryptoScamDB flagged address: {addr}. Project: {name}. Category: {category}. Status: {status}. Description: {description}"
                metadata = {
                    "source": "cryptoscamdb",
                    "address": addr.lower(),
                    "category": category,
                    "status": status,
                    "name": name,
                    "severity": "high" if status == "Active" else "medium",
                }
                if await ingest_via_api(content, metadata):
                    ingested += 1
                else:
                    errors += 1
        else:
            content = f"CryptoScamDB entry: {name or url}. Category: {category}. Status: {status}. URL: {url}. Description: {description}"
            metadata = {
                "source": "cryptoscamdb",
                "category": category,
                "status": status,
                "name": name,
                "url": url,
                "severity": "high" if status == "Active" else "medium",
            }
            if await ingest_via_api(content, metadata):
                ingested += 1
            else:
                errors += 1

        if (i + 1) % 100 == 0:
            logger.info(
                f"CryptoScamDB progress: {i + 1}/{len(scams)} | ingested: {ingested}, errors: {errors}"
            )

    return ingested, errors


async def main() -> None:
    total_ingested = 0
    total_errors = 0

    # 1. MetaMask blacklist (always accessible)
    mm_ingested, mm_errors, mm_fetch_ok = await ingest_metamask_blacklist()
    total_ingested += mm_ingested
    total_errors += mm_errors
    logger.info(f"MetaMask: {mm_ingested} docs ingested, {mm_errors} errors")

    if not mm_fetch_ok:
        # Fail loud: the primary source 404'd — surface it instead of failed=0.
        logger.error("FATAL: MetaMask phishing blacklist fetch failed")
        sys.exit(1)

    # 2. CryptoScamDB (may be down; dedicated ingest_cryptoscamdb.py fails loud)
    scams = await try_cryptoscamdb()
    if scams:
        csdb_ingested, csdb_errors = await ingest_cryptoscamdb(scams)
        total_ingested += csdb_ingested
        total_errors += csdb_errors
        logger.info(f"CryptoScamDB: {csdb_ingested} docs ingested, {csdb_errors} errors")
    else:
        logger.warning("Skipping CryptoScamDB - API unavailable")

    logger.info(f"TOTAL: {total_ingested} docs ingested, {total_errors} errors")


if __name__ == "__main__":
    asyncio.run(main())
