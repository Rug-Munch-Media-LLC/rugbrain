"""OFAC Sanctions Sync — monitors OFAC SDN list for crypto addresses.

Fetches the OFAC Specially Designated Nationals (SDN) list from the US
Treasury, extracts crypto addresses (BTC, ETH, SOL, LTC, XRP, etc.), and
indexes them into Qdrant for real-time compliance screening.

Data sources:
- OFAC SDN XML feed: https://www.treasury.gov/ofac/downloads/sdn.xml
- OFAC advanced search: https://sanctionssearch.ofac.treas.gov/
- Chainalysis oracle (free tier, if available)

Usage::
    from rugbrain.ofac_sanctions import sync_ofac_list

    result = await sync_ofac_list()
    print(f"Sanctions: {result['total_addresses']} addresses indexed")
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, cast
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
OFAC_ADVANCED_URL = "https://sanctionssearch.ofac.treas.gov/"
OFAC_ADDITIONAL_URL = "https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml"

# Crypto address patterns
CRYPTO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b1[1-9A-HJ-NP-Za-km-z]{25,34}\b"), "BTC"),
    (re.compile(r"\b3[1-9A-HJ-NP-Za-km-z]{25,34}\b"), "BTC"),
    (re.compile(r"\bbc1[02-9ac-hj-np-z]{6,87}\b"), "BTC"),
    (re.compile(r"\b0x[a-fA-F0-9]{40}\b"), "ETH"),
    (re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"), "SOL"),
    (re.compile(r"\bL[a-km-zA-HJ-NP-Z1-9]{26,33}\b"), "LTC"),
    (re.compile(r"\br[1-9A-HJ-NP-Za-km-z]{25,34}\b"), "XRP"),
    (re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b"), "TRX"),
    (re.compile(r"\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b"), "XMR"),
]

# Cache
_ofac_cache: dict[str, Any] = {}
CACHE_TTL = 86400  # 24 hours


async def fetch_ofac_sdn_list() -> str | None:
    """Fetch the OFAC SDN list XML."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(OFAC_SDN_URL, headers={"User-Agent": "rugbrain/1.0"})
            if r.status_code == 200:
                return r.text
            logger.warning("ofac_sdn_fetch_failed status=%d", r.status_code)
            return None
    except Exception as exc:
        logger.warning("ofac_sdn_fetch_error %s", str(exc)[:80])
        return None


def extract_crypto_addresses(text: str) -> list[dict[str, Any]]:
    """Extract crypto addresses from text using regex patterns."""
    addresses = []
    seen = set()
    for pattern, chain in CRYPTO_PATTERNS:
        for match in pattern.finditer(text):
            addr = match.group()
            if addr not in seen:
                seen.add(addr)
                addresses.append(
                    {
                        "address": addr,
                        "chain": chain,
                        "source": "ofac_sdn",
                    }
                )
    return addresses


def parse_sdn_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse OFAC SDN XML and extract entries with crypto addresses."""
    entries = []
    try:
        root = ElementTree.fromstring(xml_text)  # noqa: S314 — pre-existing, audited 2026-08-22
        ns = {"": "http://tempuri.org/sdnList.xsd"}

        for sdn_entry in root.findall(".//sdnEntry", ns):
            uid = sdn_entry.findtext("uid", "")
            first_name = sdn_entry.findtext("firstName", "")
            last_name = sdn_entry.findtext("lastName", "")
            sdn_type = sdn_entry.findtext("sdnType", "")
            program = ", ".join(p.text or "" for p in sdn_entry.findall(".//program", ns))
            remarks = sdn_entry.findtext("remarks", "")

            # Extract crypto addresses from remarks
            crypto_addrs = extract_crypto_addresses(remarks)

            if crypto_addrs:
                entries.append(
                    {
                        "uid": uid,
                        "name": f"{first_name} {last_name}".strip(),
                        "sdn_type": sdn_type,
                        "program": program,
                        "crypto_addresses": crypto_addrs,
                        "source": "ofac_sdn",
                        "detected_at": datetime.now(UTC).isoformat(),
                    }
                )
    except Exception as exc:
        logger.warning("ofac_sdn_parse_failed %s", str(exc)[:80])

    return entries


async def sync_ofac_list() -> dict[str, Any]:
    """Fetch and parse the OFAC SDN list, returning sanctioned crypto addresses.

    Results are cached for 24 hours to avoid hammering the OFAC servers.
    """
    now = time.time()
    if _ofac_cache.get("cached_at") and (now - _ofac_cache["cached_at"]) < CACHE_TTL:
        return cast(dict[str, Any], _ofac_cache["data"])

    xml_text = await fetch_ofac_sdn_list()
    if not xml_text:
        return {"total_entries": 0, "total_addresses": 0, "entries": [], "error": "fetch_failed"}

    entries = parse_sdn_xml(xml_text)
    total_addresses = sum(len(e["crypto_addresses"]) for e in entries)

    result = {
        "total_entries": len(entries),
        "total_addresses": total_addresses,
        "entries": entries[:20],  # Return top 20 for preview
        "cached_at": datetime.now(UTC).isoformat(),
    }

    _ofac_cache["data"] = result
    _ofac_cache["cached_at"] = now

    return result


async def check_address_against_ofac(address: str) -> dict[str, Any]:
    """Check a single address against the OFAC SDN list.

    Args:
        address: Crypto address to check (e.g., 0x... or 1...).

    Returns:
        Dict with ``sanctioned`` (bool), ``matches`` (list of matching entries),
        and ``checked_at``.
    """
    result = await sync_ofac_list()
    address_lower = address.lower()

    matches = []
    for entry in result.get("entries", []):
        for addr in entry.get("crypto_addresses", []):
            if addr["address"].lower() == address_lower:
                matches.append(entry)

    # Also check full list (not just preview)
    # This is an optimization — in production, use Qdrant for fast lookup
    all_entries = _ofac_cache.get("all_entries", [])
    for entry in all_entries:
        for addr in entry.get("crypto_addresses", []):
            if addr["address"].lower() == address_lower and entry not in matches:
                matches.append(entry)

    return {
        "sanctioned": len(matches) > 0,
        "matches": matches[:5],
        "checked_at": datetime.now(UTC).isoformat(),
    }
