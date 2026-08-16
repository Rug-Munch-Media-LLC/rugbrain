"""crypto-rekts incident knowledgebase source (``defi_exploits``).

Source: liqtags/crypto-rekts (live — probed 2026-08-16) — a structured
markdown + JSON knowledgebase mirroring rekt.news' incident database. The
repo ships a single ``rektdb.json`` (3,645 incidents as of probe, one file)
with rich fields: title, date, funds_lost, funds_returned, scam_type,
network(s), token addresses, HTML description (quick summary + exploit
details), proof links and auditor names.

Why this source: the original Rekt News RSS feed is dead (HTTP 500 — see
docs/COLLECTION_REGISTRY.md, ``hacks_v2`` is historical-only). crypto-rekts
covers the same incident universe — including governance exploits such as
Beanstalk, AnubisDAO and BUNNY DAO — with structured, maintained data, so it
is the live replacement feeding ``defi_exploits``.

Fetch: one raw.githubusercontent.com GET of ``rektdb.json`` (raw is not
subject to the unauthenticated GitHub API rate limit; ~9.6 MB).

Fail-open: any fetch/parse failure logs a warning and returns ``[]``
(consistent with ``base.py``).
Idempotent: ``doc_id`` seeds (``crypto_rekts:<id>:<content hash>``) are
deterministic; the store layer converts them to UUIDv5 point IDs so
re-ingestion overwrites the same points instead of duplicating.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "defi_exploits"
REPO = "liqtags/crypto-rekts"
REF = "main"
REKTDB_PATH = "rektdb.json"
MAX_PER_FETCH = 10_000

_HTML_TAG_RE = re.compile(r"<[^>]+>")

# scam_type label → canonical vulnerability class (mirrors the taxonomy used
# by scripts/ingest_defi_incidents.py so downstream filtering is consistent).
_CATEGORY_CLASS = {
    "reentrancy": "reentrancy",
    "flash loan attack": "flash-loan",
    "flash loan": "flash-loan",
    "oracle issue": "oracle-manipulation",
    "oracle": "oracle-manipulation",
    "access control": "access-control",
    "governance": "governance-attack",
    "rugpull": "rug-pull",
    "rug pull": "rug-pull",
    "phishing": "phishing",
    "honeypot": "honeypot",
    "private key": "key-compromise",
    "bridge": "bridge-exploit",
    "abandoned": "exit-scam",
}

_CLASS_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"reentranc", re.I), "reentrancy"),
    (re.compile(r"flash ?loan", re.I), "flash-loan"),
    (re.compile(r"oracle", re.I), "oracle-manipulation"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"access ?control", re.I), "access-control"),
    (re.compile(r"rug ?pull", re.I), "rug-pull"),
    (re.compile(r"phish", re.I), "phishing"),
    (re.compile(r"honeypot", re.I), "honeypot"),
    (re.compile(r"private ?key|key compromise", re.I), "key-compromise"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _strip_html(text: str) -> str:
    return html.unescape(_HTML_TAG_RE.sub("", text or "")).strip()


def _scam_type_label(scam_type: Any) -> str:
    """crypto-rekts ``scam_type`` is an object ``{id, type}`` or a string."""
    if isinstance(scam_type, dict):
        return str(scam_type.get("type") or "").strip()
    if isinstance(scam_type, list) and scam_type:
        return _scam_type_label(scam_type[0])
    return str(scam_type or "").strip()


def _network_names(item: dict[str, Any]) -> list[str]:
    """Extract chain/network names from the various network fields."""
    out: list[str] = []
    for key in ("scamNetworks", "networks"):
        v = item.get(key)
        if isinstance(v, list):
            for entry in v:
                if isinstance(entry, dict):
                    nw = entry.get("networks") or entry.get("network") or entry.get("name")
                    if isinstance(nw, dict):
                        name = str(nw.get("name") or "").strip()
                    else:
                        name = str(nw or "").strip()
                    if name and name not in out:
                        out.append(name)
        elif isinstance(v, dict):
            name = str(v.get("name") or "").strip()
            if name and name not in out:
                out.append(name)
    for key in ("network", "chain"):
        v = item.get(key)
        if isinstance(v, dict):
            name = str(v.get("name") or "").strip()
            if name and name not in out:
                out.append(name)
        elif isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return out


def _auditors(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    audited_by = item.get("auditedBy")
    if isinstance(audited_by, list):
        for entry in audited_by:
            if isinstance(entry, dict):
                partner = entry.get("partner")
                name = ""
                if isinstance(partner, dict):
                    name = str(partner.get("name") or "").strip()
                elif isinstance(partner, str):
                    name = partner.strip()
                if name and name not in out:
                    out.append(name)
    elif isinstance(audited_by, str) and audited_by.strip():
        out.append(audited_by.strip())
    return out


def _severity_from_loss(amount: float) -> str:
    if amount >= 10_000_000:
        return "critical"
    if amount >= 1_000_000:
        return "high"
    if amount:
        return "medium"
    return "medium"


def crypto_rekt_to_doc(item: dict[str, Any]) -> IngestDoc | None:
    """Normalize one rektdb.json item into a ``defi_exploits`` IngestDoc."""
    title = str(item.get("title") or item.get("project_name") or "").strip()
    if not title:
        return None
    incident_id = item.get("id")
    if incident_id is None:
        return None

    date = str(item.get("date") or "").strip()
    funds_lost = item.get("funds_lost") or 0
    try:
        loss_usd = float(funds_lost)
    except (TypeError, ValueError):
        loss_usd = 0.0
    funds_returned = item.get("funds_returned") or 0
    try:
        returned_usd = float(funds_returned)
    except (TypeError, ValueError):
        returned_usd = 0.0

    category = _scam_type_label(item.get("scam_type")) or "unknown"
    vuln_class = _CATEGORY_CLASS.get(category.lower(), "")
    description = _strip_html(str(item.get("description") or ""))[:6000]
    if not vuln_class:
        vuln_class = _detect_class(f"{title} {category} {description}")
    networks = _network_names(item)
    auditors = _auditors(item)
    token_addresses = item.get("token_addresses") or item.get("token_address") or []
    if isinstance(token_addresses, str):
        token_addresses = [token_addresses] if token_addresses.strip() else []

    proof_link = str(item.get("our_post_link") or item.get("proof_link") or "").strip()
    source_url = proof_link or (
        f"https://github.com/{REPO}/blob/{REF}/rekts/{re.sub(r'[^A-Za-z0-9._-]', '', title)}.md"
    )

    content_parts = [
        f"Rekt incident: {title}.",
        f"Date: {date or 'unknown'}",
        f"Funds lost: ${loss_usd:,.0f}" if loss_usd else "Funds lost: unknown",
    ]
    if returned_usd:
        content_parts.append(f"Funds returned: ${returned_usd:,.0f}")
    content_parts.append(f"Category: {category}")
    content_parts.append(f"Vulnerability class: {vuln_class or 'unknown'}")
    if networks:
        content_parts.append("Networks: " + ", ".join(networks))
    if auditors:
        content_parts.append("Audited by: " + ", ".join(auditors))
    if token_addresses:
        content_parts.append("Token addresses: " + ", ".join(str(a) for a in token_addresses[:20]))
    if description:
        content_parts.append(f"\n{description}")

    doc_id = f"crypto_rekts:{incident_id}:{_content_hash(title + date + category)}"

    metadata: dict[str, Any] = {
        "collection": DEFAULT_COLLECTION,
        "protocol": title,
        "date": date,
        "loss_usd": loss_usd,
        "funds_returned_usd": returned_usd,
        "category": category,
        "vulnerability_class": vuln_class or "unknown",
        "severity": _severity_from_loss(loss_usd),
        "chain": networks[0] if networks else "unknown",
        "ecosystem": "multi",
        "source": "crypto_rekts",
        "source_url": source_url,
        "doc_type": "post_mortem",
    }
    if networks:
        metadata["networks"] = networks
    if auditors:
        metadata["audited_by"] = auditors
    if token_addresses:
        metadata["token_addresses"] = [str(a) for a in token_addresses]

    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=doc_id,
        metadata=metadata,
        doc_type="post_mortem",
        source="crypto_rekts",
        title=f"{title} — rekt incident",
        url=source_url,
    )


def _detect_class(text: str) -> str:
    for pattern, cls in _CLASS_KEYWORDS:
        if pattern.search(text or ""):
            return cls
    return ""


def parse_rektdb(text: str) -> list[dict[str, Any]]:
    """Parse the raw ``rektdb.json`` into a list of incident dicts.

    Tolerates both the full envelope (``{"items": [...], "count": N}``) and
    a bare list; malformed payloads return [].
    """
    import json

    try:
        data = json.loads(text or "")
    except (ValueError, TypeError):
        return []
    if isinstance(data, dict):
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        return []
    return [d for d in items if isinstance(d, dict)]


class CryptoRektsSource(Source):
    """liqtags/crypto-rekts — structured rekt.news incident knowledgebase.

    Single-file ``rektdb.json`` fetch; emits one ``post_mortem`` doc per
    incident into ``defi_exploits``.
    """

    name = "crypto_rekts"
    doc_type = "post_mortem"
    default_collection = DEFAULT_COLLECTION
    description = "crypto-rekts knowledgebase of rekt.news incidents (live mirror; defi_exploits)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        text = await fetch_raw(client, REPO, REF, REKTDB_PATH)
        if not text:
            log.warning("crypto_rekts_fetch_failed (empty rektdb.json)")
            return []
        items = parse_rektdb(text)
        if not items:
            log.warning("crypto_rekts_parse_empty (no items in rektdb.json)")
            return []
        docs: list[IngestDoc] = []
        for item in items:
            doc = crypto_rekt_to_doc(item)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        if not docs:
            log.warning("crypto_rekts_no_docs (items=%d)", len(items))
        return docs


__all__ = [
    "REPO",
    "CryptoRektsSource",
    "crypto_rekt_to_doc",
    "parse_rektdb",
]
