"""ossf/malicious-packages — OpenSSF MAL-* advisory database.

Source: https://github.com/ossf/malicious-packages — OpenSSF's curated
database of malicious packages found on public registries, published as OSV
advisories under ``osv/malicious/<ecosystem>/<package>/MAL-*.json``
(incl. Solana/ETH key-stealing packages such as ``solana-crypto-validator``
and ``fast_log``). Each advisory carries an OSV record: id, summary,
details, affected packages (ecosystem + name) and aliases.

Routes to ``crypto_vulnerabilities`` (vulnerability registry collection),
doc_type ``vulnerability_pattern`` — supply-chain crypto malware is
vulnerability knowledge, not incident knowledge.

Design rules (consistent with ``base.py`` / ``github.py``):

  * Fetch → normalize → emit; fail-open per advisory file.
  * Deterministic ``doc_id`` (``ossf_malicious_packages:<MAL-id>``) — OSV
    IDs are stable → idempotent re-ingestion.
  * Bounded by ``limit`` (default cap 2,000) — the repo holds thousands of
    advisories.
  * Credentials: env-only (``GITHUB_TOKEN`` / ``PROXY_URL``).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "crypto_vulnerabilities"
DEFAULT_CAP = 2_000
REPO = "ossf/malicious-packages"
REF = "main"
OSV_DIR = "osv/malicious"
SOURCE_URL = f"https://github.com/{REPO}"


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _affected_first(advisory: dict[str, Any]) -> dict[str, str]:
    affected = advisory.get("affected")
    if isinstance(affected, list) and affected and isinstance(affected[0], dict):
        pkg = affected[0].get("package") or {}
        if isinstance(pkg, dict):
            return {
                "ecosystem": str(pkg.get("ecosystem") or "").strip(),
                "package": str(pkg.get("name") or "").strip(),
            }
    return {"ecosystem": "", "package": ""}


def osv_to_doc(rel_path: str, advisory: dict[str, Any]) -> IngestDoc | None:
    """Normalize one OSV advisory into a malicious-package IngestDoc."""
    advisory_id = str(advisory.get("id") or "").strip()
    if not advisory_id:
        return None
    summary = str(advisory.get("summary") or "").strip()
    details = str(advisory.get("details") or "").strip()
    pkg = _affected_first(advisory)
    aliases = advisory.get("aliases") or []
    if isinstance(aliases, list):
        aliases = [str(a) for a in aliases if str(a)]

    content_parts = [f"Malicious package advisory: {advisory_id}."]
    if summary:
        content_parts.append(f"Summary: {summary}")
    if pkg["package"]:
        content_parts.append(f"Package: {pkg['package']} ({pkg['ecosystem'] or 'unknown'})")
    if details:
        content_parts.append(f"Details:\n{details[:4000]}")
    if aliases:
        content_parts.append("Aliases: " + ", ".join(aliases))
    content_parts.append(f"Source: {SOURCE_URL}/blob/{REF}/{rel_path}")

    severity = "high"
    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"ossf_malicious_packages:{advisory_id}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "advisory_id": advisory_id,
            "ecosystem": pkg["ecosystem"] or "unknown",
            "package": pkg["package"],
            "category": "malicious-package",
            "severity": severity,
            "source_url": f"{SOURCE_URL}/blob/{REF}/{rel_path}",
            "aliases": aliases,
            "doc_type": "vulnerability_pattern",
        },
        doc_type="vulnerability_pattern",
        source="ossf_malicious_packages",
        title=summary or f"Malicious package {advisory_id}",
        url=f"{SOURCE_URL}/blob/{REF}/{rel_path}",
    )


class OSSFMaliciousPackagesSource(Source):
    """ossf/malicious-packages — OpenSSF MAL-* advisories.

    Lists the repo tree, fetches ``osv/malicious/**/MAL-*.json`` advisories
    (bounded by ``limit``), and emits one ``IngestDoc`` per advisory into
    ``crypto_vulnerabilities`` (doc_type ``vulnerability_pattern``).
    """

    name = "ossf_malicious_packages"
    doc_type = "vulnerability_pattern"
    default_collection = DEFAULT_COLLECTION
    description = "OpenSSF malicious-packages — MAL-* supply-chain crypto malware advisories (crypto_vulnerabilities)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("ossf_malicious_tree_failed (rate-limited or repo moved)")
            return []
        advisories = max_size(
            pick_files(entries, suffixes=(".json",), include=(f"{OSV_DIR}/",)), cap
        )
        docs: list[IngestDoc] = []
        for e in advisories:
            path = str(e.get("path", ""))
            text = await fetch_raw(client, REPO, REF, path)
            try:
                advisory = json.loads(text) if text else {}
            except json.JSONDecodeError:
                advisory = {}
            doc = osv_to_doc(path, advisory)
            if doc:
                docs.append(doc)
            if len(docs) >= cap:
                break
        if not docs:
            log.warning("ossf_malicious_fetch_empty")
        else:
            log.info("ossf_malicious_feed_ok advisories=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "OSSFMaliciousPackagesSource",
    "osv_to_doc",
]
