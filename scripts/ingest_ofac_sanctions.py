#!/usr/bin/env python3
"""Ingest OFAC SDN sanctions list into the ``crime_reports`` collection.

Source: https://www.treasury.gov/ofac/downloads/sdn.xml — US Treasury Specially
Designated Nationals (SDN) list, published as XML. Contains crypto addresses
(BTC, ETH, SOL, LTC, XRP, TRX, XMR) for sanctioned individuals/entities.

Target: crime_reports (EMPTY, 0 points, owned canonical 4096 base lane).
This collection is the canonical crime-reports write target. Currently 0 points —
completely empty. This ingest will populate it with sanctioned crypto addresses.

Each sanctioned entity with crypto addresses is embedded (4096-dim qwen3-embedding-8b
via DeepInfra primary, llama.cpp local fallback) and upserted with filterable
metadata: name, program, uid, crypto type, extracted address, OFAC source.

Idempotent: point IDs are deterministic seeds (``ofac:{uid}:{address_lower}``) which
the store converts to UUIDv5 point IDs, so re-running overwrites the same points.

Usage:
    python scripts/ingest_ofac_sanctions.py --dry-run
    python scripts/ingest_ofac_sanctions.py --limit 50
    python scripts/ingest_ofac_sanctions.py           # full list

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - Canonical embedder (DeepInfra primary via DEEPINFRA_API_KEY,
    local llama.cpp :8004 fallback)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Use the canonical 4096-dim embedder (deepinfra -> local fallback)
from rugbrain.embedder import get_embedder
from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_ofac_sanctions")

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
COLLECTION = "crime_reports"
SOURCE = "ofac_sdn"
SOURCE_URL = "https://home.treasury.gov/policy-issues/financial-sanctions/specially-designated-nationals-sdn-list"
EMBED_DIM = 4096  # canonical qwen3-embedding-8b
REQUEST_TIMEOUT = 60.0

# Crypto address regex patterns (mirrors ofac_sanctions.py in src)
# Order matters: more specific patterns first to avoid generic SOL pattern catching others
CRYPTO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b1[1-9A-HJ-NP-Za-km-z]{25,34}\b"), "BTC"),
    (re.compile(r"\b3[1-9A-HJ-NP-Za-km-z]{25,34}\b"), "BTC"),
    (re.compile(r"\bbc1[02-9ac-hj-np-z]{6,87}\b"), "BTC"),
    (re.compile(r"\b0x[a-fA-F0-9]{40}\b"), "ETH"),
    (re.compile(r"\bL[a-km-zA-HJ-NP-Z1-9]{26,33}\b"), "LTC"),
    (re.compile(r"\br[1-9A-HJ-NP-Za-km-z]{25,34}\b"), "XRP"),
    (re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b"), "TRX"),
    (re.compile(r"\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b"), "XMR"),
    (re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"), "SOL"),  # SOL last, generic base58
]

CACHE_FILE = Path("/tmp/ofac_sdn_cache.xml")


async def _fetch_ofac_sdn_xml(use_cache: bool = True) -> str | None:
    """Fetch the OFAC SDN XML list."""
    if use_cache and await asyncio.to_thread(CACHE_FILE.exists):
        mtime = (await asyncio.to_thread(CACHE_FILE.stat)).st_mtime
        age_hours = (datetime.now(UTC).timestamp() - mtime) / 3600
        if age_hours < 24:
            logger.info("ofac_sdn_cache_hit age=%.1fh", age_hours)
            return await asyncio.to_thread(CACHE_FILE.read_text, encoding="utf-8")

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as c:
            r = await c.get(
                OFAC_SDN_URL, follow_redirects=True, headers={"User-Agent": "rugbrain/1.0"}
            )
            if r.status_code == 200:
                xml_text = r.text
                # Cache for 24h
                await asyncio.to_thread(CACHE_FILE.write_text, xml_text, encoding="utf-8")
                return xml_text
            else:
                logger.warning("ofac_fetch_failed status=%d", r.status_code)
                return None
    except Exception as e:
        logger.warning("ofac_fetch_error %s", str(e)[:100])
        return None


def _extract_crypto_addresses(text: str) -> list[dict[str, Any]]:
    """Extract crypto addresses from text using regex patterns."""
    addresses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern, chain in CRYPTO_PATTERNS:
        for match in pattern.finditer(text):
            addr = match.group()
            addr_lower = addr.lower()
            if addr_lower not in seen:
                seen.add(addr_lower)
                addresses.append(
                    {
                        "address": addr,
                        "address_lower": addr_lower,
                        "chain": chain,
                    }
                )
    return addresses


def _parse_sdn_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse OFAC SDN XML and extract entries with crypto addresses."""
    import xml.etree.ElementTree as ET

    ns = {"": "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML"}

    entries: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 — trusted HTTPS source (OFAC)
    except Exception as e:
        logger.warning("ofac_xml_parse_failed %s", str(e)[:80])
        return entries

    for sdn_entry in root.findall(".//sdnEntry", ns):
        uid = sdn_entry.findtext("uid", "", ns)
        first_name = sdn_entry.findtext("firstName", "", ns)
        last_name = sdn_entry.findtext("lastName", "", ns)
        sdn_type = sdn_entry.findtext("sdnType", "", ns)
        program = ", ".join(p.text or "" for p in sdn_entry.findall(".//program", ns))
        remarks = sdn_entry.findtext("remarks", "", ns)

        crypto_addrs = _extract_crypto_addresses(remarks or "")

        name = f"{first_name} {last_name}".strip() or uid or "Unknown"
        entries.append(
            {
                "uid": uid or f"entry_{len(entries)}",
                "name": name,
                "sdn_type": sdn_type,
                "program": program,
                "crypto_addresses": crypto_addrs,
                "source": SOURCE,
                "source_url": SOURCE_URL,
            }
        )

    return entries


def _build_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """Build filterable metadata from parsed entry."""
    return {
        "name": entry.get("name", "")[:200],
        "uid": entry.get("uid", ""),
        "sdn_type": entry.get("sdn_type", "")[:100],
        "program": entry.get("program", "")[:200],
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "crypto_count": len(entry.get("crypto_addresses", [])),
        "knowledge_type": "ofac_sanction",
    }


async def run(dry_run: bool, limit: int | None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "source": SOURCE,
        "fetched": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    # Fetch OFAC SDN XML
    xml_text = await _fetch_ofac_sdn_xml(use_cache=False)
    if not xml_text:
        logger.error("ofac_fetch_no_xml")
        stats["errors"] += 1
        return stats

    stats["fetched"] = 1
    entries = _parse_sdn_xml(xml_text)
    logger.info("ofac_parsed_entries total=%d", len(entries))

    if not entries:
        logger.warning("ofac_no_crypto_addrs")
        if dry_run:
            return stats
        # Still embed the non-crypto entries if any
        entries = [
            {
                "uid": "no_crypto",
                "name": "No crypto addresses found",
                "sdn_type": "xml",
                "program": "full-list",
                "crypto_addresses": [],
                "source": SOURCE,
                "source_url": SOURCE_URL,
            }
        ]

    # Limit entries if requested
    if limit:
        entries = entries[:limit]
        logger.info("ofac_limit_to %d", limit)

    if dry_run:
        # In dry-run mode, we still embed but do not upsert to Qdrant
        embedder = get_embedder()
        stats["embedded"] = 0
        for entry in entries:
            # Build text content for embedding
            parts = []
            if entry.get("name"):
                parts.append(f"Name: {entry['name']}")
            if entry.get("sdn_type"):
                parts.append(f"Type: {entry['sdn_type']}")
            if entry.get("program"):
                parts.append(f"Program: {entry['program']}")

            addr_parts = []
            for addr_entry in entry.get("crypto_addresses", []):
                addr_parts.append(f"{addr_entry['chain']}:{addr_entry['address']}")
            if addr_parts:
                parts.append(f"Sanctioned crypto addresses: {'; '.join(addr_parts)}")
            else:
                parts.append("No crypto addresses in remarks")

            text_content = "\n".join(parts)

            # Embed (but do not upsert in dry-run)
            try:
                vec = await embedder.embed([text_content])
                if vec and vec[0]:
                    stats["embedded"] += 1
                else:
                    stats["errors"] += 1
            except Exception as e:
                logger.warning(
                    "ofac_embed_failed name=%s err=%s",
                    entry.get("name", "unknown")[:40],
                    str(e)[:120],
                )
                stats["errors"] += 1
                continue

        logger.info(
            "dry_run fetched=%d embedded=%d upserted=0 errors=%d",
            stats["fetched"],
            stats["embedded"],
            stats["errors"],
        )
        return stats

    # Embed and upsert
    embedder = get_embedder()
    stats["embedded"] = 0

    for entry in entries:
        # Build text content for embedding
        parts = []
        if entry.get("name"):
            parts.append(f"Name: {entry['name']}")
        if entry.get("sdn_type"):
            parts.append(f"Type: {entry['sdn_type']}")
        if entry.get("program"):
            parts.append(f"Program: {entry['program']}")

        addr_parts = []
        for addr_entry in entry.get("crypto_addresses", []):
            addr_parts.append(f"{addr_entry['chain']}:{addr_entry['address']}")
        if addr_parts:
            parts.append(f"Sanctioned crypto addresses: {'; '.join(addr_parts)}")
        else:
            parts.append("No crypto addresses in remarks")

        text_content = "\n".join(parts)

        # Embed
        try:
            vec = await embedder.embed([text_content])
            if not vec or not vec[0]:
                stats["errors"] += 1
                continue
            vec = vec[0]
            stats["embedded"] += 1
        except Exception as e:
            logger.warning(
                "ofac_embed_failed name=%s err=%s", entry.get("name", "unknown")[:40], str(e)[:120]
            )
            stats["errors"] += 1
            continue

        # Deterministic doc_id
        uid = entry.get("uid", "unknown")
        # Use first crypto address lower as part of ID for address-specific points
        first_addr = ""
        for addr_entry in entry.get("crypto_addresses", []):
            first_addr = addr_entry["address_lower"]
            break
        doc_id = f"ofac:{uid}:{first_addr or 'nokey'}"

        # Metadata
        metadata = _build_metadata(entry)

        # Upsert to Qdrant
        store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
        ok = await store.insert(
            doc_id=doc_id,
            collection=COLLECTION,
            embedding=vec,
            content=text_content,
            metadata=metadata,
            source=SOURCE,
            severity="critical",
            chain="multi",
        )
        if ok:
            stats["upserted"] += 1
        else:
            stats["errors"] += 1
        await store.close()

    logger.info(
        "ofac_done fetched=%d embedded=%d upserted=%d errors=%d dry_run=%s",
        stats["fetched"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="OFAC SDN list → crime_reports")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + embed but do not upsert")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to ingest")
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit))
    import json

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
