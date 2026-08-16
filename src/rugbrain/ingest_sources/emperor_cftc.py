"""emperor-mew/cftc-actions — CFTC press releases + enforcement classification.

Source: https://huggingface.co/datasets/emperor-mew/cftc-actions — every
CFTC press release with enforcement-action keyword classification (4,400+
releases 2003-2026, 2,500+ enforcement). The HF datasets-server exposes no
parsed rows for this dataset (verified 2026-08-16), so the source consumes
the dataset's live companion API instead:

    https://api.ai-analytics.org/api/v1/cftc/recent

which returns a JSON-LD ``ItemList`` of the most recent press releases with
``release_number``, ``title``, ``release_date``, ``url``, ``is_enforcement``,
``enforcement_type`` and ``type_label`` (CC0-licensed data).

Routes to ``regulation_enforcement`` (the SEC/DOJ/CFTC enforcement
collection), doc_type ``enforcement_action``.

NOTE [REVIEW]: this is a third-party companion API (api.ai-analytics.org),
not a formally documented public API; the ``recent`` window serves the
latest 50 releases and pagination/backfill params are not confirmed. Treat
as a rolling-freshness feed, not a full backfill.

Design rules (consistent with ``base.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``emperor_mew_cftc_actions:<release_number>``)
    keeps re-ingestion idempotent.
  * Credentials: none.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "regulation_enforcement"
DEFAULT_CAP = 500
API_URL = "https://api.ai-analytics.org/api/v1/cftc/recent"
SOURCE_URL = "https://huggingface.co/datasets/emperor-mew/cftc-actions"


def _extract_items(data: Any) -> list[dict[str, Any]]:
    """Tolerate the JSON-LD ItemList envelope (``items`` list)."""
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [d for d in items if isinstance(d, dict)]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def release_to_doc(item: dict[str, Any]) -> IngestDoc | None:
    """Normalize one CFTC press release into an enforcement-action doc."""
    release_number = str(item.get("release_number") or "").strip()
    title = str(item.get("title") or "").strip()
    if not release_number and not title:
        return None
    release_date = str(item.get("release_date") or "").strip()
    url = str(item.get("url") or "").strip()
    is_enforcement = item.get("is_enforcement")
    enforcement_type = str(item.get("enforcement_type") or "").strip()
    type_label = str(item.get("type_label") or "").strip()

    content_parts = [f"CFTC press release: {title or release_number}."]
    if release_date:
        content_parts.append(f"Release date: {release_date}")
    if is_enforcement not in (None, ""):
        content_parts.append(f"Enforcement action: {'yes' if is_enforcement else 'no'}")
    if enforcement_type:
        content_parts.append(f"Enforcement type: {enforcement_type}")
    if type_label:
        content_parts.append(f"Type label: {type_label}")
    if url:
        content_parts.append(f"Source: {url}")

    severity = "critical" if is_enforcement else "medium"
    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"emperor_mew_cftc_actions:{release_number or _stable_key(title, release_date)}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "agency": "CFTC",
            "release_number": release_number,
            "release_date": release_date,
            "is_enforcement": bool(is_enforcement),
            "enforcement_type": enforcement_type,
            "type_label": type_label,
            "severity": severity,
            "source_url": url or SOURCE_URL,
            "doc_type": "enforcement_action",
        },
        doc_type="enforcement_action",
        source="emperor_mew_cftc_actions",
        title=f"CFTC: {title or release_number}",
        url=url or SOURCE_URL,
    )


def _stable_key(title: str, date: str) -> str:
    """Deterministic fallback key when release_number is absent."""
    import hashlib

    raw = f"{title}:{date}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


class EmperorMewCFTCSource(Source):
    """emperor-mew/cftc-actions — CFTC press releases + enforcement labels.

    Emits one ``IngestDoc`` per press release into ``regulation_enforcement``
    (doc_type ``enforcement_action``) via the dataset's companion API.
    """

    name = "emperor_mew_cftc_actions"
    doc_type = "enforcement_action"
    default_collection = DEFAULT_COLLECTION
    description = "CFTC press releases with enforcement classification (emperor-mew/cftc-actions, regulation_enforcement)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(API_URL, timeout=self.timeout)
            data = r.json() if r.status_code == 200 else {}
        except Exception as e:
            log.warning("emperor_cftc_fetch_error err=%s", str(e)[:200])
            data = {}
        items = _extract_items(data)
        docs = [d for item in items if (d := release_to_doc(item)) is not None]
        if not docs:
            log.warning("emperor_cftc_fetch_empty")
        else:
            log.info("emperor_cftc_feed_ok releases=%d (cap=%d)", len(docs), cap)
        return docs[:cap]


__all__ = [
    "DEFAULT_COLLECTION",
    "EmperorMewCFTCSource",
    "_extract_items",
    "release_to_doc",
]
