"""Chainabuse scam-report source (``crime_reports``).

Source: Chainabuse (https://chainabuse.com) — a community scam-reporting
platform (TRM Labs) that aggregates reports of phishing, pig-butchering,
romance scams, fake exchanges and other crypto fraud. This source fetches
LIVE published reports from the Chainabuse public API and normalizes them
into ``crime_reports`` docs (the same collection the curated
``scripts/ingest_chainabuse.py`` taxonomy seeds).

Endpoint: ``GET https://api.chainabuse.com/v0/reports`` — live, auth-gated.
Probed 2026-08-16: without a key it returns HTTP 401 ``{"message":"Invalid
credentials"}``, i.e. the endpoint exists and requires an API key (the older
``/v1/reports`` path used by ``scripts/ingest_chainabuse.py`` returns 404).

Auth: ``CHAINABUSE_API_KEY`` env sent as a Bearer token.
``CHAINABUSE_API_URL`` overrides the base URL. Without a key the source
degrades gracefully: it logs a warning and returns ``[]`` (consistent with
``base.py`` fail-open design) so a missing credential never aborts a batch.

Fail-open: any network/parse failure logs a warning and returns ``[]``.
Idempotent: ``doc_id`` seeds (``chainabuse:<report id>:<content hash>``) are
deterministic; the store layer converts them to UUIDv5 point IDs so
re-ingestion overwrites the same points instead of duplicating.

The response shape evolves; the parser is intentionally tolerant of both a
bare list and a ``{"reports": [...]}`` envelope, and accepts several
field-name candidates per concept.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "crime_reports"
BASE_URL = os.getenv("CHAINABUSE_API_URL", "https://api.chainabuse.com").rstrip("/")
REPORTS_PATH = "/v0/reports"
DEFAULT_PAGE_SIZE = 100  # API accepts a bounded per-request limit
MAX_PER_FETCH = 1_000

# Report category → severity (mirrors the curated taxonomy in
# scripts/ingest_chainabuse.py so downstream filtering is consistent).
_CATEGORY_SEVERITY = {
    "phishing": "critical",
    "wallet-drainer": "critical",
    "wallet drainer": "critical",
    "fake-exchange": "critical",
    "fake exchange": "critical",
    "investment-scam": "critical",
    "investment scam": "critical",
    "romance-scam": "critical",
    "romance scam": "critical",
    "impersonation": "high",
    "giveaway": "high",
}


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _first(d: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string value among *keys* (tolerant field reads)."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, dict):
            v = v.get("name") or v.get("type") or v.get("value") or ""
        if isinstance(v, list) and v:
            v = v[0]
        s = str(v).strip()
        if s and s.lower() != "null":
            return s
    return ""


def _scam_type_value(scam_type: Any) -> str:
    """Chainabuse ``scam_type`` is often an object ``{id, type}`` or a string."""
    if isinstance(scam_type, dict):
        return str(scam_type.get("type") or scam_type.get("name") or "").strip()
    if isinstance(scam_type, list) and scam_type:
        return _scam_type_value(scam_type[0])
    return str(scam_type or "").strip()


def _extract_reports(data: Any) -> list[dict[str, Any]]:
    """Tolerate a bare list, ``{"reports": [...]}`` or ``{"items": [...]}`` envelope."""
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for key in ("reports", "items", "data"):
            v = data.get(key)
            if isinstance(v, list):
                return [d for d in v if isinstance(d, dict)]
    return []


def report_to_doc(report: dict[str, Any]) -> IngestDoc | None:
    """Normalize one Chainabuse report into a ``crime_reports`` IngestDoc.

    Returns ``None`` for reports without any identifiable entity (address,
    domain or description) so empty upstream rows never create blank points.
    """
    category = _scam_type_value(report.get("scam_type")) or _first(report, "category")
    description = _first(report, "description")
    address = _first(report, "reported_address", "address", "wallet_address", "scam_address")
    domain = _first(report, "scam_url", "domain", "url", "website")
    chain = _first(report, "chain", "network")
    date = _first(report, "date", "created_at", "reported_at", "report_date")
    status = _first(report, "status")

    entity = address or domain or ""
    if not entity and not description:
        return None

    report_id = _first(report, "id", "report_id") or _content_hash(entity + description[:200])
    category_label = category or "unknown"
    severity = _CATEGORY_SEVERITY.get(category_label.lower(), "high" if category else "medium")

    content_parts = [
        f"Chainabuse report: {category_label}.",
        f"Description: {description or '(no description)'}",
    ]
    if entity:
        content_parts.append(f"Reported entity: {entity} ({'address' if address else 'domain'})")
    if chain:
        content_parts.append(f"Chain/network: {chain}")
    if date:
        content_parts.append(f"Reported: {date}")
    if status:
        content_parts.append(f"Status: {status}")

    metadata: dict[str, Any] = {
        "collection": DEFAULT_COLLECTION,
        "category": category_label,
        "attack_vector": category_label,
        "severity": severity,
        "source": "chainabuse",
        "source_url": "https://chainabuse.com",
        "knowledge_type": "crime_report",
        "ecosystem": "multi",
    }
    if address:
        metadata["address"] = address
        metadata["entity_type"] = "address"
        metadata["entity"] = address
    if domain:
        metadata["domain"] = domain
        metadata.setdefault("entity_type", "domain")
        metadata.setdefault("entity", domain)
    if chain:
        metadata["chain"] = chain
    if date:
        metadata["reported_date"] = date

    doc_id = f"chainabuse:{report_id}:{_content_hash(entity + category_label + description[:500])}"
    title = f"Chainabuse report — {category_label}"
    if address:
        title += f" ({address[:10]}…)"
    elif domain:
        title += f" ({domain})"
    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=doc_id,
        metadata=metadata,
        doc_type="crime_report",
        source="chainabuse",
        title=title,
        url="https://chainabuse.com",
    )


class ChainabuseSource(Source):
    """Chainabuse public API — live crowdsourced scam reports.

    Requires ``CHAINABUSE_API_KEY`` (env); without it the source fails open
    with a warning and returns ``[]`` so a batch never aborts.
    """

    name = "chainabuse"
    doc_type = "crime_report"
    default_collection = DEFAULT_COLLECTION
    description = "Chainabuse crowdsourced scam reports (live API, auth-gated; crime_reports)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        api_key = os.getenv("CHAINABUSE_API_KEY", "").strip()
        if not api_key:
            log.warning("chainabuse_no_api_key (CHAINABUSE_API_KEY unset; returning [])")
            return []
        cap = MAX_PER_FETCH
        if limit is not None:
            cap = min(limit, MAX_PER_FETCH)
        page_size = min(max(cap, 1), DEFAULT_PAGE_SIZE)
        url = f"{BASE_URL}{REPORTS_PATH}"
        try:
            r = await client.get(
                url,
                params={"limit": page_size},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                log.warning(
                    "chainabuse_fetch_status status=%d url=%s",
                    r.status_code,
                    url,
                )
                return []
            data = r.json()
        except Exception as e:
            log.warning("chainabuse_fetch_error err=%s", str(e)[:200])
            return []

        docs: list[IngestDoc] = []
        for report in _extract_reports(data):
            doc = report_to_doc(report)
            if doc:
                docs.append(doc)
            if len(docs) >= cap:
                break
        if not docs:
            log.warning("chainabuse_fetch_empty (no reports parsed)")
        return docs


__all__ = [
    "BASE_URL",
    "REPORTS_PATH",
    "ChainabuseSource",
    "_extract_reports",
    "report_to_doc",
]
