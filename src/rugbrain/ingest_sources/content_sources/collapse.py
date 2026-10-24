"""Collapse cross-source duplicates into one canonical item with every citation.

The detector is not re-implemented: ``cluster_duplicates`` from
:mod:`rugbrain.ingest_sources.ingest_dedupe` is the same function the news
channel uses (exact normalised hash first, token-shingle Jaccard above
``DUP_THRESHOLD``). What this module adds is the citation side — a cluster of N
copies becomes ONE item whose metadata names every source and URL that carried
it, because corroboration is evidence and must survive the collapse.

Each item passed in is a ``news_articles`` document as produced by
:func:`rugbrain.ingest_sources.content_sources.parity.to_news_item`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugbrain.ingest_sources.ingest_dedupe import DUP_THRESHOLD, cluster_duplicates

__all__ = ["collapse_items"]


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Bridge object: the detector reads ``.text``, we keep the whole item."""

    text: str
    item: dict[str, Any]


def _citation(member: _Candidate) -> dict[str, str]:
    meta = member.item.get("metadata") or {}
    return {
        "source": str(meta.get("source") or ""),
        "url": str(meta.get("url") or ""),
    }


def _rank(member: _Candidate) -> tuple[float, int]:
    """Richest copy wins: importance first, then body length."""
    meta = member.item.get("metadata") or {}
    return (float(meta.get("importance") or 0.0), len(member.text))


def collapse_items(
    items: list[dict[str, Any]], *, threshold: float = DUP_THRESHOLD
) -> list[dict[str, Any]]:
    """Collapse duplicates, keeping the richest copy and all citations."""
    candidates = [_Candidate(str(item.get("content") or ""), item) for item in items]
    collapsed: list[dict[str, Any]] = []
    for cluster in cluster_duplicates(candidates, threshold=threshold):
        best = max(cluster, key=_rank)
        metadata = dict(best.item.get("metadata") or {})
        citations: list[dict[str, str]] = []
        for member in cluster:
            citation = _citation(member)
            if citation not in citations:
                citations.append(citation)
        metadata["citations"] = citations
        metadata["corroboration"] = len(citations)
        collapsed.append({**best.item, "metadata": metadata})
    collapsed.sort(key=lambda item: str(item["metadata"].get("published") or ""), reverse=True)
    return collapsed
