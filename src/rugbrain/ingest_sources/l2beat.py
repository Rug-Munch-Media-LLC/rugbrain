"""L2BEAT risk framework source (l2beat/l2beat).

Source: https://github.com/l2beat/l2beat — the L2BEAT project tracks risk
analysis for layer-2s and bridges. Per-project risk assessments live in
TypeScript config files under ``packages/config/src/projects/`` (one file
per project: ``display.description`` + ``riskView`` areas like
``stateValidation``, ``dataAvailability``, ``exitWindow``, …).

This source fetches those TS project files and normalizes each into a
doc_type ``l2_risk`` document: a structured summary of the risk areas +
the raw (bounded) config so downstream chunking keeps the code context.

Rate-limit behavior matches the other GitHub sources: log + return ``[]``.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

REPO = "l2beat/l2beat"
REF = "main"
DEFAULT_COLLECTION = "l2_risk"
DOC_TYPE = "l2_risk"

# Per-project config files (one TS file per L2/bridge project).
INCLUDE_DIRS = ("packages/config/src/projects/",)
RAW_CAP = 100_000

_EXPORT_RE = re.compile(r"export\s+const\s+(\w+)\s*:\s*(?:Layer2|Bridge|Other)\b")
_RISK_AREA_RE = re.compile(r"(\w+):\s*\{[^{}]*?description:\s*['\"]([^'\"]*)['\"]")
_DISPLAY_DESC_RE = re.compile(r"description:\s*['\"]([^'\"]{20,})['\"]")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _extract_block(text: str, marker: str) -> str:
    """Return the brace-balanced ``{...}`` block that starts at *marker*."""
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace : i + 1]
    return text[brace:]


def _project_name(rel_path: str, ts: str) -> str:
    m = _EXPORT_RE.search(ts)
    if m:
        return m.group(1)
    return rel_path.split("/")[-1].replace(".ts", "")


def extract_risk_areas(ts: str) -> list[tuple[str, str]]:
    """Parse ``riskView`` area name → description pairs from a L2BEAT TS file."""
    block = _extract_block(ts, "riskView:")
    if not block:
        return []
    areas: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _RISK_AREA_RE.finditer(block):
        name = m.group(1)
        desc = m.group(2).strip()
        key = f"{name}:{desc[:80]}"
        if not desc or key in seen:
            continue
        seen.add(key)
        areas.append((name, desc))
    return areas[:16]


def extract_display_description(ts: str) -> str:
    """First ``description: '...'`` string inside the ``display`` block."""
    block = _extract_block(ts, "display:")
    if not block:
        return ""
    m = _DISPLAY_DESC_RE.search(block)
    return m.group(1).strip() if m else ""


def l2beat_project_to_doc(rel_path: str, ts: str) -> IngestDoc | None:
    """Normalize one L2BEAT project TS file into an ``l2_risk`` doc."""
    if not ts.strip():
        return None
    name = _project_name(rel_path, ts)
    areas = extract_risk_areas(ts)
    display_desc = extract_display_description(ts)
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"

    parts = [f"L2BEAT risk framework — {name}."]
    if display_desc:
        parts.append(f"Description: {display_desc}")
    if areas:
        parts.append("Risk areas:")
        for area, desc in areas:
            parts.append(f"  - {area}: {desc}")
    summary = "\n".join(parts)
    if len(ts) <= RAW_CAP:
        body = f"\n\n```typescript\n{ts}\n```"
    else:
        body = f"\n\n```typescript\n{ts[:RAW_CAP]}\n```"
    return IngestDoc(
        content=summary + body,
        doc_id=f"l2beat:{name}:{_content_hash(ts[:4000])}",
        doc_type=DOC_TYPE,
        source="l2beat",
        title=name,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "project": name,
            "risk_areas": [area for area, _ in areas],
            "has_risk_view": bool(areas),
            "rel_path": rel_path,
            "source_url": source_url,
            "chain": "EVM",
            "ecosystem": "Cross-Chain",
            "doc_type": DOC_TYPE,
        },
    )


class L2BeatSource(Source):
    """L2BEAT per-project risk assessments (layer-2s + bridges)."""

    name = "l2beat"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "L2BEAT risk framework — per-project L2/bridge risk assessments"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("l2beat_no_entries (rate-limited or repo moved)")
            return []
        project_entries = max_size(
            pick_files(
                entries,
                suffixes=(".ts",),
                include=INCLUDE_DIRS,
            ),
            limit,
        )
        docs: list[IngestDoc] = []
        for e in project_entries:
            path = str(e.get("path", ""))
            if ".test." in path or ".spec." in path:
                continue
            ts = await fetch_raw(client, REPO, REF, path)
            doc = l2beat_project_to_doc(path, ts)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REPO",
    "L2BeatSource",
    "extract_display_description",
    "extract_risk_areas",
    "l2beat_project_to_doc",
]
