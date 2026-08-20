"""RugBrain — smarter ingestion helpers for the knowledge pipeline.

Shared building blocks used by ``scripts/ingest_scam_knowledge.py`` (and any
future ingester) to make ingestion:

  * deduplicated   — chunks are cross-referenced against existing Qdrant
                     content by vector similarity; chunks with a top hit
                     >= ``dedup_threshold`` (default 0.95) are skipped.
  * smart chunked  — text is split on incident/attack boundaries (headings,
                     ``contract`` declarations, blank-line paragraphs) before
                     windowing, instead of raw byte slicing.
  * freshness aware— git-backed sources are refreshed with ``git pull`` and
                     report their ``last_updated`` commit timestamp so only
                     changed files are re-ingested (file-hash checkpointing).
  * priority aware — exploit/critical content is ingested before reference
                     material (severity-ordered + optional severity filter).

Pure/async helpers are importable and unit-testable without external
services (Qdrant interactions accept an ``httpx.AsyncClient``).
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Sequence
from typing import Any

import httpx

log = logging.getLogger("rugbrain.ingest_smart")

# ── Smart chunking ───────────────────────────────────────────────────────

# Incident boundaries: markdown headings, "Incident"/"Case study"/"Hack"
# paragraphs, and Solidity ``contract X {`` declarations.
_INCIDENT_HEADING_RE = re.compile(r"(?m)^(?=#{1,4}\s)")
_INCIDENT_WORD_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(incident|case\s*stud|post[- ]mortem|hack|exploit|attack|vuln[a-z]*)\s*[:#.]?",
)
_CONTRACT_RE = re.compile(r"(?m)^\s*contract\s+[A-Za-z_$][\w$]*\s*[{]")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")


def split_incident_sections(text: str) -> list[str]:
    """Split *text* into incident/attack sections where boundaries exist.

    Splits on markdown headings first, then explicit incident keywords
    (``## Incident 1``, ``Case study:``, ``Post-mortem``), then Solidity
    ``contract`` declarations. Falls back to paragraph boundaries, then
    returns the whole text as one section.
    """
    text = (text or "").strip()
    if not text:
        return []

    sections = [s for s in re.split(_INCIDENT_HEADING_RE, text) if s.strip()]
    if len(sections) > 1:
        return [s.strip() for s in sections if s.strip()]

    # Split on incident keyword lines (keeps the keyword with its section).
    marker_matches = list(_INCIDENT_WORD_RE.finditer(text))
    if len(marker_matches) > 1:
        parts: list[str] = []
        for i, m in enumerate(marker_matches):
            start = m.start()
            end = marker_matches[i + 1].start() if i + 1 < len(marker_matches) else len(text)
            chunk = text[start:end].strip()
            if chunk:
                parts.append(chunk)
        if len(parts) > 1:
            return parts

    contract_matches = list(_CONTRACT_RE.finditer(text))
    if len(contract_matches) > 1:
        parts = []
        for i, m in enumerate(contract_matches):
            start = m.start()
            end = contract_matches[i + 1].start() if i + 1 < len(contract_matches) else len(text)
            chunk = text[start:end].strip()
            if chunk:
                parts.append(chunk)
        if len(parts) > 1:
            return parts

    paragraphs = [p.strip() for p in re.split(_PARAGRAPH_RE, text) if p.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    return [text]


def smart_split(text: str, max_len: int = 700, overlap: int = 70) -> list[str]:
    """Split *text* into searchable chunks.

    Incident-aware first (``split_incident_sections``), then window any
    section that is still too long. Prefers sentence boundaries inside a
    window when possible.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    for section in split_incident_sections(text):
        if len(section) <= max_len:
            chunks.append(section)
            continue
        chunks.extend(_window(section, max_len, overlap))
    return [c for c in chunks if c.strip()]


def _window(text: str, max_len: int, overlap: int) -> list[str]:
    """Fixed-width windows with overlap, biased to sentence boundaries."""
    out: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_len, n)
        chunk = text[start:end]
        if end < n:
            # Back off to the last sentence boundary within the window.
            boundary = max(chunk.rfind(". "), chunk.rfind("\n"), chunk.rfind("; "))
            if boundary > max_len // 2:
                end = start + boundary + 1
                chunk = text[start:end]
        if chunk.strip():
            out.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return out


# ── Priority ordering ────────────────────────────────────────────────────

SEVERITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
    "unknown": 5,
}

# Exploit-first source ordering: incident data before reference/taxonomy.
SOURCE_PRIORITY: dict[str, int] = {
    "defihacklabs": 0,
    "rekt_news": 1,
    "defillama_hacks": 1,
    "web3hacks": 2,
    "postmortems": 2,
    "immunefi": 3,
    "cryptoscamdb": 3,
    "slowmist": 4,
    "swc_registry": 5,
    "openzeppelin_audits": 6,
}


def _doc_priority(doc: dict[str, Any]) -> tuple[int, int]:
    """Return ``(source_rank, severity_rank)`` for a doc dict.

    Doc dicts follow the ingest-script shape: ``{"metadata": {...}, ...}``.
    """
    metadata = doc.get("metadata") or {}
    severity = str(metadata.get("severity", "unknown")).strip().lower()
    sev_rank = SEVERITY_RANK.get(severity, SEVERITY_RANK["unknown"])
    source = str(metadata.get("source_type", metadata.get("source", ""))).strip().lower()
    src_rank = SOURCE_PRIORITY.get(source, 10)
    return (src_rank, sev_rank)


def sort_by_priority(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort docs so exploit/critical content is ingested first."""
    return sorted(docs, key=_doc_priority)


def filter_by_severity(
    docs: Sequence[dict[str, Any]], min_severity: str = "high"
) -> list[dict[str, Any]]:
    """Keep only docs whose severity is at least ``min_severity``.

    Ordering: critical < high < medium < low < info < unknown.
    """
    min_rank = SEVERITY_RANK.get(min_severity.strip().lower(), SEVERITY_RANK["medium"])
    kept: list[dict[str, Any]] = []
    for doc in docs:
        metadata = doc.get("metadata") or {}
        severity = str(metadata.get("severity", "unknown")).strip().lower()
        rank = SEVERITY_RANK.get(severity, SEVERITY_RANK["unknown"])
        if rank <= min_rank:
            kept.append(doc)
    return kept


# ── Vector dedup against Qdrant ──────────────────────────────────────────


async def dedup_vectors(
    embeddings: Sequence[list[float] | None],
    collection: str,
    client: httpx.AsyncClient,
    threshold: float = 0.95,
    qdrant_url: str = "http://127.0.0.1:6334",
) -> list[bool]:
    """Return a boolean mask: True when the chunk already exists in Qdrant.

    Each embedding is searched against ``collection``; a top hit with score
    >= ``threshold`` marks the chunk as a duplicate (skip). ``None``
    embeddings (embedding failure) are treated as not-duplicate so they are
    not silently dropped twice.
    """
    if not embeddings:
        return []
    base = qdrant_url.rstrip("/")
    masks: list[bool] = []
    for vec in embeddings:
        if vec is None:
            masks.append(False)
            continue
        try:
            r = await client.post(
                f"{base}/collections/{collection}/points/search",
                json={
                    "vector": vec,
                    "limit": 1,
                    "score_threshold": threshold,
                    "with_payload": False,
                },
            )
            if r.status_code != 200:
                masks.append(False)
                continue
            hits = r.json().get("result", []) or []
            masks.append(bool(hits) and float(hits[0].get("score", 0.0)) >= threshold)
        except Exception as e:
            log.warning("dedup_search_failed: %s", e)
            masks.append(False)
    return masks


async def is_duplicate_vector(
    embedding: list[float],
    collection: str,
    client: httpx.AsyncClient,
    threshold: float = 0.95,
    qdrant_url: str = "http://127.0.0.1:6334",
) -> bool:
    """Return True when *embedding* already has a >= threshold match."""
    return (await dedup_vectors([embedding], collection, client, threshold, qdrant_url))[0]


# ── Git freshness ────────────────────────────────────────────────────────


def git_refresh(repo_dir: str, timeout: int = 300) -> bool:
    """``git pull --ff-only`` an existing clone. Returns True on success.

    Non-blocking safety: runs via subprocess with a timeout; any failure
    (network, auth, conflicts) is logged and treated as "no refresh" — the
    ingester then uses the existing checkout.
    """
    try:
        result = subprocess.run(  # noqa: S603 — repo_dir is caller-provided path
            ["git", "-C", repo_dir, "pull", "--ff-only", "--quiet"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            log.info("git_refresh ok repo=%s", repo_dir)
            return True
        log.warning("git_refresh_failed repo=%s err=%s", repo_dir, result.stderr[-300:])
        return False
    except Exception as e:
        log.warning("git_refresh_error repo=%s err=%s", repo_dir, str(e)[:200])
        return False


def repo_last_updated(repo_dir: str) -> str | None:
    """Return the newest commit timestamp (ISO-8601) of a git repo, or None."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", repo_dir, "log", "-1", "--format=%cI"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
        )
        stamp = result.stdout.strip()
        return stamp or None
    except Exception as e:
        log.debug("repo_last_updated_error: %s", e)
        return None


__all__ = [
    "filter_by_severity",
    "git_refresh",
    "is_duplicate_vector",
    "repo_last_updated",
    "smart_split",
    "sort_by_priority",
    "split_incident_sections",
]
