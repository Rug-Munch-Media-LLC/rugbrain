"""security-alliance/seal-911 — SEAL 911 incident-resolution log.

Source: https://github.com/security-alliance/seal-911 — SEAL 911 is the
samczsun-led whitehat emergency-response group for crypto incidents. The
repo's ``ACTIVITY.md`` is a continually evolving public record of incidents
in which SEAL 911 played a pivotal role (protocol exploit rescues, domain
hijackings, stolen-fund freezes). Each entry is a markdown link:
``- [<Title>](<url>)``.

Routes to ``threat_intel_v2`` (the live threat-intel collection) — the
entries are real-time emergency-response evidence, not historical
post-mortems.

Design rules (consistent with ``base.py`` / ``incidents.py``):

  * Fetch ``ACTIVITY.md`` → parse one ``IngestDoc`` per incident resolution.
  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``seal_911:<slug>:<hash>``) keeps re-ingestion
    idempotent.
  * Credentials: none — raw.githubusercontent content.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "threat_intel_v2"
DEFAULT_CAP = 2_000
REPO = "security-alliance/seal-911"
REF = "main"
ACTIVITY_URL = f"https://raw.githubusercontent.com/{REPO}/{REF}/ACTIVITY.md"
SOURCE_URL = f"https://github.com/{REPO}/blob/{REF}/ACTIVITY.md"

# ``- [Title](url)`` — markdown link lines (possibly prefixed by task markers).
_LINK_RE = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\((\S+?)\)\s*$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Seal911Entry:
    """One SEAL 911 incident-resolution record."""

    title: str
    url: str
    row_index: int


def parse_activity_log(markdown: str) -> list[Seal911Entry]:
    """Parse ``ACTIVITY.md`` link lines into incident-resolution records."""
    entries: list[Seal911Entry] = []
    for line in (markdown or "").splitlines():
        m = _LINK_RE.match(line)
        if not m:
            continue
        title = m.group(1).strip()
        url = m.group(2).strip()
        if not title or not url:
            continue
        entries.append(Seal911Entry(title=title, url=url, row_index=len(entries)))
    return entries


def seal911_to_doc(entry: Seal911Entry) -> IngestDoc:
    """Normalize one incident-resolution record into an IngestDoc."""
    slug = _SLUG_RE.sub("-", entry.title.lower()).strip("-") or f"entry-{entry.row_index}"
    return IngestDoc(
        content=(f"SEAL 911 incident resolution: {entry.title}.\nResolution source: {entry.url}\n"),
        doc_id=f"seal_911:{slug}:{_content_hash(entry.url)}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": entry.title,
            "category": "whitehat-response",
            "severity": "high",
            "chain": "unknown",
            "ecosystem": "multi",
            "source_url": entry.url,
            "doc_type": "incident",
        },
        doc_type="incident",
        source="seal_911",
        title=f"SEAL 911: {entry.title}",
        url=entry.url,
    )


class Seal911Source(Source):
    """security-alliance/seal-911 — whitehat emergency-response incident log.

    Emits one ``IngestDoc`` per resolution entry into ``threat_intel_v2``
    (doc_type ``incident``).
    """

    name = "seal_911"
    doc_type = "incident"
    default_collection = DEFAULT_COLLECTION
    description = "SEAL 911 incident-resolution log (whitehat emergency responses, threat_intel_v2)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(ACTIVITY_URL, timeout=self.timeout)
            markdown = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("seal911_fetch_error err=%s", str(e)[:160])
            markdown = ""
        entries = parse_activity_log(markdown)
        if not entries:
            log.warning("seal911_fetch_empty")
            return []
        log.info("seal911_feed_ok entries=%d (cap=%d)", len(entries), cap)
        return [seal911_to_doc(e) for e in entries[:cap]]


__all__ = [
    "DEFAULT_COLLECTION",
    "Seal911Entry",
    "Seal911Source",
    "parse_activity_log",
    "seal911_to_doc",
]
