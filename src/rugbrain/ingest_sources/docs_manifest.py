"""Read the public-docs manifest and expand it into canonical document ids.

The manifest (``config/bots_launchpads_manifest.json``) is the single source of
truth for WHICH public pages are ingested and WHAT provenance they carry:
publisher, licence class, doc_type, category, retrieved date and canonical id.
This module only reads and expands it — no network, no normalisation.
``bot_launchpad_docs`` owns fetching, ``scripts/ingest_bots_launchpads.py``
owns embedding.

Two entry modes keep the crawl bounded:

``document``  one URL → one document.
``sitemap``   the sitemap is read at FETCH time and capped by ``max_pages``, so
              a 3,654-page documentation site cannot turn one source into an
              unbounded crawl.

A blocked entry (403, dead DNS, no public docs) is recorded in the manifest
rather than silently dropped: a source we cannot read is a finding, not a gap.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).resolve().parents[3] / "config" / "bots_launchpads_manifest.json"
_MODE_DOCUMENT = "document"
_MODE_SITEMAP = "sitemap"
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ManifestEntry:
    """One manifest row, validated at load time."""

    id: str
    publisher: str
    category: str
    doc_type: str
    license: str
    retrieved_on: str
    mode: str
    url: str
    max_pages: int = 1


def manifest_path() -> Path:
    """Manifest location, overridable with ``BOT_DOCS_MANIFEST``."""
    override = os.getenv("BOT_DOCS_MANIFEST", "").strip()
    return Path(override) if override else MANIFEST_PATH


def canonical_id(entry: ManifestEntry, url: str | None = None) -> str:
    """Stable id: the entry id, plus the URL slug for a sitemap page."""
    if entry.mode == _MODE_DOCUMENT or url is None:
        return entry.id
    slug = _SLUG.sub("-", url.split("://", 1)[-1].lower()).strip("-")
    return f"{entry.id}:{slug[:96]}"


def _entry(obj: dict[str, Any]) -> ManifestEntry:
    mode = str(obj.get("mode", _MODE_DOCUMENT))
    if mode not in (_MODE_DOCUMENT, _MODE_SITEMAP):
        raise ValueError(f"manifest entry {obj.get('id')!r}: unknown mode {mode!r}")
    url = str(obj.get("url", "")).strip()
    if not url.startswith("https://"):
        raise ValueError(f"manifest entry {obj.get('id')!r}: url must be https")
    missing = [k for k in ("id", "publisher", "doc_type", "license", "retrieved_on") if not obj.get(k)]
    if missing:
        raise ValueError(f"manifest entry {obj.get('id')!r}: missing {missing}")
    return ManifestEntry(
        id=str(obj["id"]),
        publisher=str(obj["publisher"]),
        category=str(obj.get("category", "")),
        doc_type=str(obj["doc_type"]),
        license=str(obj["license"]),
        retrieved_on=str(obj["retrieved_on"]),
        mode=mode,
        url=url,
        max_pages=max(int(obj.get("max_pages", 1)), 1),
    )


def load_manifest(path: Path | None = None) -> tuple[ManifestEntry, ...]:
    """Load, validate and de-duplicate the manifest entries (by ``id``).

    A duplicate id is a hard error: two rows writing the same canonical id
    would make the corpus count depend on dict ordering.
    """
    data = json.loads((path or manifest_path()).read_text(encoding="utf-8"))
    entries = tuple(_entry(o) for o in data.get("entries", []))
    seen: set[str] = set()
    for e in entries:
        if e.id in seen:
            raise ValueError(f"duplicate manifest id {e.id!r}")
        seen.add(e.id)
    return entries


__all__ = [
    "MANIFEST_PATH",
    "ManifestEntry",
    "canonical_id",
    "load_manifest",
    "manifest_path",
]
