"""SWC Registry source — Smart Contract Weakness Classification entries.

Source: https://github.com/SmartContractSecurity/SWC-registry
Each ``entries/docs/SWC-<id>.md`` entry classifies a smart-contract weakness:
title, description, remediation, Solidity samples and CWE relationships.
These are the canonical EVM weakness taxonomy.

doc_type: ``vulnerability_pattern``. Reuses the canonical attack-vector and
severity maps from ``rugbrain.crypto_schema`` (same values as
``scripts/ingest_swc_registry.py``).
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.crypto_schema import SWC_SEVERITY, SWC_VECTORS
from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

REPO = "SmartContractSecurity/SWC-registry"
REF = "master"
DEFAULT_COLLECTION = "threat_intel"
DOC_TYPE = "vulnerability_pattern"

# Fallback IDs when the GitHub API is rate-limited.
FALLBACK_SWC_IDS = [f"SWC-{i}" for i in range(100, 137)]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _section(markdown: str, heading: str) -> str:
    """Extract text under a ``## heading`` (until the next ``## ``)."""
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
    )
    m = pattern.search(markdown or "")
    return m.group(1).strip() if m else ""


def extract_title(markdown: str) -> str:
    """Extract the weakness title (``# Title`` marker, then first heading)."""
    m = re.search(r"^# Title\s*\n+\s*([^\n]+)", markdown or "", re.MULTILINE)
    if m:
        return str(m.group(1)).strip()
    for heading in re.findall(r"^# (.+)$", markdown or "", re.MULTILINE):
        heading = str(heading)
        if (
            heading.lower() not in ("title",)
            and "no longer actively maintained" not in heading.lower()
        ):
            return heading.strip()
    return ""


def extract_solidity_samples(markdown: str) -> list[str]:
    """Extract deduplicated ```solidity code blocks (max 3)."""
    samples: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"```solidity\s*\n(.*?)```", markdown or "", re.DOTALL):
        code = m.group(1).strip()
        key = _content_hash(code[:200])
        if code and key not in seen:
            seen.add(key)
            samples.append(code)
        if len(samples) >= 3:
            break
    return samples


def extract_cwe(markdown: str) -> list[str]:
    """Extract ``CWE-<id>`` references from the Relationships section."""
    return re.findall(r"CWE-\d+", _section(markdown, "Relationships"))


def swc_markdown_to_doc(swc_id: str, markdown: str) -> IngestDoc | None:
    """Transform one SWC markdown entry into a structured weakness doc."""
    title = extract_title(markdown) or swc_id
    description = _section(markdown, "Description")
    remediation = _section(markdown, "Remediation")
    samples = extract_solidity_samples(markdown)
    cwe = extract_cwe(markdown)
    if not description and not samples:
        log.debug("swc_empty_entry swc=%s", swc_id)
        return None

    vector = SWC_VECTORS.get(swc_id, "smart-contract-exploit")
    severity = SWC_SEVERITY.get(swc_id, "high")
    parts = [
        f"{swc_id} — {title}. Smart Contract Weakness Classification entry.",
        f"Attack vector: {vector}. Severity: {severity}.",
    ]
    if cwe:
        parts.append(f"CWE relationships: {', '.join(cwe)}.")
    if description:
        parts.append(f"Description: {description}")
    if remediation:
        parts.append(f"Remediation: {remediation}")
    content = " ".join(parts)
    if samples:
        content += "\n\n" + "\n\n".join(f"```solidity\n{s}\n```" for s in samples)

    source_url = f"https://github.com/{REPO}/blob/{REF}/entries/docs/{swc_id}.md"
    return IngestDoc(
        content=content,
        doc_id=f"swc:{swc_id}:{_content_hash(content[:4000])}",
        doc_type=DOC_TYPE,
        source="swc_registry",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "swc_id": swc_id,
            "protocol": swc_id,
            "category": "smart-contract",
            "attack_vector": vector,
            "severity": severity,
            "chain": "ethereum",
            "cwe": cwe,
            "has_sample_code": bool(samples),
            "source_type": "swc_registry",
            "source_url": source_url,
            "doc_type": DOC_TYPE,
        },
    )


class SWCSource(Source):
    """GitHub SWC Registry weakness classifications."""

    name = "swc_registry"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Smart Contract Weakness Classification (SWC) taxonomy entries"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, REPO, REF)
        if entries:
            swc_entries = pick_files(entries, suffixes=(".md",), include=("entries/docs/",))
            swc_entries = max_size(swc_entries, limit)
            filenames = [str(e.get("path", "")).split("/")[-1] for e in swc_entries]
        else:
            # Rate-limited: fall back to known IDs; the entries exist on master.
            filenames = [f"{swc}.md" for swc in FALLBACK_SWC_IDS]
            if limit:
                filenames = filenames[:limit]

        docs: list[IngestDoc] = []
        for filename in filenames:
            swc_id = filename.replace(".md", "").upper()
            if not swc_id.startswith("SWC-"):
                continue
            path = f"entries/docs/{filename}"
            markdown = await fetch_raw(client, REPO, REF, path)
            doc = swc_markdown_to_doc(swc_id, markdown)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "FALLBACK_SWC_IDS",
    "REPO",
    "SWCSource",
    "extract_cwe",
    "extract_solidity_samples",
    "extract_title",
    "swc_markdown_to_doc",
]
