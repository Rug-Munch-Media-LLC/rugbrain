#!/usr/bin/env python3
"""
Ingest CryptoScamDB blacklist data into RAG known_scams collection.
Source: https://github.com/CryptoScamDB/blacklist (data/urls.yaml on master).
The public API (api.cryptoscamdb.org) has been intermittently 502 — the GitHub
YAML dataset is the maintained canonical source. The local snapshot under
data/ is refreshed from GitHub when missing or older than 7 days.
"""

import asyncio
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_cryptoscamdb_blacklist")

RAG_API = "http://localhost:8000/api/v1/rag/ingest"
COLLECTION = "known_scams"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
GITHUB_YAML_URL = "https://raw.githubusercontent.com/CryptoScamDB/blacklist/master/data/urls.yaml"
YAML_MAX_AGE_SECONDS = 7 * 24 * 3600


def _ensure_yaml() -> bool:
    """Refresh the local CryptoScamDB YAML snapshot from GitHub when missing/stale.

    Sync (file/network I/O) — call via asyncio.to_thread. Returns True if a
    local snapshot is available after the attempt.
    """
    yaml_path = os.path.join(DATA_DIR, "cryptoscamdb_urls.yaml")
    if os.path.exists(yaml_path):
        try:
            age = time.time() - os.path.getmtime(yaml_path)
            if age < YAML_MAX_AGE_SECONDS:
                return True
            logger.info(
                f"Local CryptoScamDB YAML is {int(age // 86400)} days old — refreshing from GitHub"
            )
        except OSError:
            pass
    else:
        logger.info("Local CryptoScamDB YAML missing — downloading from GitHub")

    try:
        import httpx

        with httpx.Client(timeout=60) as client:
            resp = client.get(GITHUB_YAML_URL)
            if resp.status_code != 200:
                logger.error(f"Failed to download CryptoScamDB YAML: HTTP {resp.status_code}")
                return os.path.exists(yaml_path)
            text = resp.text
    except Exception as e:
        logger.error(f"Failed to download CryptoScamDB YAML: {e}")
        return os.path.exists(yaml_path)

    tmp_path = f"{yaml_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp_path, yaml_path)
    logger.info(f"Refreshed CryptoScamDB YAML snapshot from GitHub ({len(text)} bytes)")
    return True


def make_doc_id(address: str, source: str) -> str:
    return hashlib.sha256(f"known_scams:{address.lower()}:{source}".encode()).hexdigest()[:16]


async def ingest_via_api(content: str, metadata: dict) -> bool:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                RAG_API, json={"collection": COLLECTION, "content": content, "metadata": metadata}
            )
            return resp.status_code == 200
    except Exception:
        return False


async def ingest_yaml():
    """Parse the YAML blacklist and ingest entries with addresses."""
    yaml_path = os.path.join(DATA_DIR, "cryptoscamdb_urls.yaml")
    if not Path(yaml_path).exists():  # noqa: ASYNC240
        logger.error(f"YAML file not found: {yaml_path}")
        return {"ingested": 0, "no_address": 0}

    # Simple YAML parser (avoid pyyaml dependency for this structure)
    entries = []
    current = {}
    with open(yaml_path, encoding="utf-8", errors="replace") as f:  # noqa: ASYNC230
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("- name:"):
                if current:
                    entries.append(current)
                current = {"name": stripped[7:].strip().strip('"')}
            elif stripped.startswith("  url:"):
                current["url"] = stripped[6:].strip().strip('"')
            elif stripped.startswith("  category:"):
                current["category"] = stripped[11:].strip().strip('"')
            elif stripped.startswith("  subcategory:"):
                current["subcategory"] = stripped[14:].strip().strip('"')
            elif stripped.startswith("  description:"):
                current["description"] = stripped[14:].strip().strip('"')
            elif stripped.startswith("  reporter:"):
                current["reporter"] = stripped[11:].strip().strip('"')
            elif stripped.startswith("    ETH:"):
                current["chain"] = "ethereum"
            elif stripped.startswith(('    - "0x', "    - 0x")):
                addr = (
                    stripped.split('"')[-2] if '"' in stripped else stripped.split("-")[-1].strip()
                )
                current.setdefault("addresses", []).append(addr)  # type: ignore[arg-type,attr-defined]
            elif stripped.startswith("    BTC:"):
                current["chain"] = "bitcoin"
            elif stripped.startswith(("    BSC:", "    BNB:")):
                current["chain"] = "bsc"
        if current:
            entries.append(current)

    logger.info(f"Parsed {len(entries)} entries from YAML")

    ingested = 0
    no_address = 0

    for i, entry in enumerate(entries):
        name = entry.get("name", "")
        category = entry.get("category", "unknown")
        subcategory = entry.get("subcategory", "")
        description = entry.get("description", "")
        url = entry.get("url", "")
        addresses = entry.get("addresses", [])

        if not addresses:
            # Ingest URL-based entry without blockchain address
            content = f"CryptoScamDB: {name}. Category: {category}/{subcategory}. URL: {url}. {description}"
            metadata = {
                "source": "cryptoscamdb",
                "category": category.lower(),
                "subcategory": subcategory.lower(),
                "name": name,
                "url": url,
                "severity": "high" if category == "Phishing" else "medium",
            }
            if await ingest_via_api(content, metadata):
                no_address += 1
        else:
            for addr in addresses:
                if not addr or len(addr) < 10:
                    continue
                chain = entry.get("chain", "ethereum")
                content = f"CryptoScamDB flagged address: {addr} on {chain}. Project: {name}. Category: {category}/{subcategory}. {description}"
                metadata = {
                    "source": "cryptoscamdb",
                    "address": addr.lower(),
                    "chain": chain,
                    "category": category.lower(),
                    "subcategory": subcategory.lower(),
                    "name": name,
                    "url": url,
                    "severity": "high" if category in ("Phishing", "Scam") else "medium",
                }
                if await ingest_via_api(content, metadata):
                    ingested += 1

        if (i + 1) % 500 == 0:
            logger.info(
                f"Progress: {i + 1}/{len(entries)} | addr_entries: {ingested}, url_only: {no_address}"
            )
            await asyncio.sleep(0.1)

    logger.info(f"COMPLETE: addr_entries={ingested}, url_only={no_address}")
    return {"ingested": ingested, "no_address": no_address}


async def main():
    yaml_available = await asyncio.to_thread(_ensure_yaml)
    result = await ingest_yaml()
    logger.info(f"Final: {result}")

    # Fail loud: no local snapshot and no fresh download means the blacklist
    # step contributed nothing — surface it instead of reporting ok.
    if not yaml_available or (result["ingested"] == 0 and result["no_address"] == 0):
        logger.error("CryptoScamDB blacklist ingest produced no data")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
