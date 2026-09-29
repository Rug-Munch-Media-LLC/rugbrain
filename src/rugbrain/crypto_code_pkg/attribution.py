"""Result attribution for the crypto-code path.

One job: turn a raw Qdrant point into a result dict that carries its source —
which library, which file, which verdict label, and why that verdict. The
retriever's job is to RANK points; attribution is what makes a rank auditable.
"""

from __future__ import annotations

import re
from typing import Any

# Human-readable verdict labels for attribution.
VERDICT_LABELS: dict[str, str] = {
    "clean": "reference implementation (clean)",
    "malicious": "exploit / malicious pattern",
    "defense": "defensive pattern",
    "weakness": "known weakness",
    "mixed": "mixed verdict",
    "unknown": "unclassified",
}

#: Payload fields the keyword score reads.
_KEYWORD_FIELDS = ("title", "description", "code_block", "content", "tags", "library_id")


def keyword_score(query: str, payload: dict[str, Any]) -> float:
    """BM25-ish keyword overlap between *query* and a candidate payload.

    Not a true BM25 (no IDF/corpus stats) — a term-coverage ratio over the
    searchable text fields, used as the second fusion list.
    """
    terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
    if not terms:
        return 0.0
    haystack = " ".join(str(payload.get(f, "") or "") for f in _KEYWORD_FIELDS).lower()
    return sum(1 for t in terms if t in haystack) / len(terms)


def verdict_label(verdict: str) -> str:
    """Human-readable label for a verdict, defaulting to 'unclassified'."""
    return VERDICT_LABELS.get((verdict or "unknown").lower(), VERDICT_LABELS["unknown"])


def format_hit(
    point: dict[str, Any],
    collection: str,
    dense_score: float,
    keyword_score_value: float,
) -> dict[str, Any]:
    """Normalize a raw Qdrant point into an attributed result."""
    payload = point.get("payload", {}) or {}
    provenance = payload.get("provenance") or {}
    verdict = str(payload.get("verdict") or "unknown").lower()
    result: dict[str, Any] = {
        "id": point.get("id"),
        "collection": collection,
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "code_block": payload.get("code_block") or payload.get("content", ""),
        "library": payload.get("library_id") or payload.get("library_name") or "",
        "file_path": payload.get("file_path", ""),
        "snippet_type": payload.get("snippet_type", ""),
        "language": payload.get("language", ""),
        "verdict": verdict,
        "verdict_label": verdict_label(verdict),
        "category": payload.get("category", ""),
        "severity": payload.get("severity", ""),
        "ecosystem": payload.get("ecosystem", ""),
        "source_url": payload.get("source") or provenance.get("source_url", ""),
        "dense_score": round(dense_score, 4),
        "keyword_score": round(keyword_score_value, 4),
    }
    reason_why = payload.get("reason_why")
    if reason_why:
        result["reason_why"] = reason_why
    return result


def augment_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Project an attributed hit onto the generic search-result shape.

    This is the public-facing projection: raw ContextCrypto coordinates must
    not leak to public coding search, so the internal fields stay inside the
    internal retriever and only these are merged upstream.
    """
    return {
        "content": hit.get("code_block") or hit.get("description", ""),
        "similarity": hit.get("dense_score", 0),
        "source": hit.get("source_url", ""),
        "source_collection": hit.get("collection"),
        "contextcrypto": True,
        "library": hit.get("library", ""),
        "verdict": hit.get("verdict", "unknown"),
        "verdict_label": hit.get("verdict_label", ""),
        "file_path": hit.get("file_path", ""),
        "reason_why": hit.get("reason_why"),
        "rrf_score": hit.get("rrf_score", 0),
    }
