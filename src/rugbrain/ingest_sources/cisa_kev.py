"""CISA KEV — Known Exploited Vulnerabilities catalog (crypto_vulnerabilities).

Source: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
— CISA's catalog of vulnerabilities known to be exploited in the wild,
covering the infrastructure Web3 platforms depend on (chain nodes,
exchanges, wallets, tooling). 1,665 entries live (verified 2026-08-16).

One ``IngestDoc`` per CVE into ``crypto_vulnerabilities`` (doc_type
``vulnerability_pattern``) with CISA KEV metadata (vendor, product, due
date, ransomware-campaign flag).

Design rules (consistent with ``base.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``cisa_kev:<cveID>``) — CVE IDs are stable →
    idempotent re-ingestion.
  * Credentials: none.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "crypto_vulnerabilities"
DEFAULT_CAP = 5_000
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
SOURCE_URL = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"


def _extract_vulnerabilities(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        vulns = data.get("vulnerabilities")
        if isinstance(vulns, list):
            return [v for v in vulns if isinstance(v, dict)]
    return []


def kev_to_doc(vuln: dict[str, Any]) -> IngestDoc | None:
    """Normalize one KEV entry into a vulnerability IngestDoc."""
    cve_id = str(vuln.get("cveID") or "").strip()
    if not cve_id:
        return None
    vendor = str(vuln.get("vendorProject") or "").strip()
    product = str(vuln.get("product") or "").strip()
    name = str(vuln.get("vulnerabilityName") or "").strip()
    description = str(vuln.get("shortDescription") or "").strip()
    date_added = str(vuln.get("dateAdded") or "").strip()
    due_date = str(vuln.get("dueDate") or "").strip()
    ransomware = vuln.get("knownRansomwareCampaignUse")
    required_action = str(vuln.get("requiredAction") or "").strip()

    content_parts = [f"CISA KEV vulnerability: {cve_id}."]
    if name:
        content_parts.append(f"Name: {name}")
    if vendor:
        content_parts.append(f"Vendor: {vendor}")
    if product:
        content_parts.append(f"Product: {product}")
    if description:
        content_parts.append(f"Description: {description}")
    if date_added:
        content_parts.append(f"Date added: {date_added}")
    if due_date:
        content_parts.append(f"Due date: {due_date}")
    if ransomware not in (None, ""):
        content_parts.append(f"Known ransomware campaign: {ransomware}")
    if required_action:
        content_parts.append(f"Required action: {required_action}")

    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"cisa_kev:{cve_id}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "cve_id": cve_id,
            "vendor": vendor,
            "product": product,
            "vulnerability_name": name,
            "date_added": date_added,
            "due_date": due_date,
            "known_ransomware_campaign": bool(ransomware),
            "category": "known-exploited-vulnerability",
            "severity": "high",
            "source_url": SOURCE_URL,
            "doc_type": "vulnerability_pattern",
        },
        doc_type="vulnerability_pattern",
        source="cisa_kev",
        title=f"{cve_id} — {name or vendor or 'KEV vulnerability'}",
        url=SOURCE_URL,
    )


class CISAKevSource(Source):
    """CISA KEV catalog — known exploited vulnerabilities.

    Emits one ``IngestDoc`` per CVE into ``crypto_vulnerabilities``
    (doc_type ``vulnerability_pattern``).
    """

    name = "cisa_kev"
    doc_type = "vulnerability_pattern"
    default_collection = DEFAULT_COLLECTION
    description = (
        "CISA Known Exploited Vulnerabilities catalog (CVE knowledge, crypto_vulnerabilities)"
    )
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(KEV_URL, timeout=self.timeout)
            data = r.json() if r.status_code == 200 else {}
        except Exception as e:
            log.warning("cisa_kev_fetch_error err=%s", str(e)[:200])
            data = {}
        vulns = _extract_vulnerabilities(data)
        docs = [d for v in vulns if (d := kev_to_doc(v)) is not None]
        if not docs:
            log.warning("cisa_kev_fetch_empty")
        else:
            log.info("cisa_kev_feed_ok vulns=%d (cap=%d)", len(docs), cap)
        return docs[:cap]


__all__ = [
    "DEFAULT_COLLECTION",
    "CISAKevSource",
    "_extract_vulnerabilities",
    "kev_to_doc",
]
