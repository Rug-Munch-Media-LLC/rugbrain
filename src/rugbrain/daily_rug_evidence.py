"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Evidence collection and sufficiency for the Daily Rug brief.

RAG first, always: every paragraph of a Daily Rug brief must trace to a
retrieved document with a URL. Two properties are enforced here, before a
single sentence is written:

*   **Every evidence row has a URL.** A hit without one cannot be cited, so it
    is dropped rather than paraphrased into an unsourced claim.
*   **Two independent sources, two publishers.** One wire story syndicated
    across ten domains is one source. Sufficiency counts distinct domains.

An evidence shortfall is a first-class result, not an exception: the caller
returns ``insufficient_evidence`` with this module's reason string attached.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urlparse

#: Families that may carry story evidence. Reference corpora are excluded:
#: they are undated and would let a 2019 incident into a 2026 brief.
EVIDENCE_COLLECTIONS: tuple[str, ...] = (
    "news_v2",
    "research_v2",
    "news_articles",
    "social_intel",
)

#: Retrieval floor. Deliberately low — the cross-encoder does the triage.
DEFAULT_MIN_SIMILARITY = 0.3

#: Characters of a hit kept as its supporting snippet.
SNIPPET_CHARS = 420

#: (query, collections=, limit=, min_similarity=) -> hits.
SearchFn = Callable[..., Awaitable[Sequence[dict[str, Any]]]]


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= SNIPPET_CHARS:
        return flat
    cut = flat[:SNIPPET_CHARS].rsplit(". ", 1)[0]
    return (cut or flat[:SNIPPET_CHARS]).rstrip(" .,") + "."


def _publisher(url: str, metadata: dict[str, Any]) -> str:
    named = str(metadata.get("publisher") or metadata.get("source") or "").strip()
    if named:
        return named
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def normalise_hit(hit: dict[str, Any], index: int) -> dict[str, Any] | None:
    """Flatten one search hit, or return None when it cannot be cited."""
    metadata = hit.get("metadata") or {}
    url = str(metadata.get("url") or metadata.get("source_url") or hit.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    body = str(hit.get("content") or metadata.get("text") or metadata.get("title") or "")
    title = str(metadata.get("title") or body.split("\n", 1)[0]).strip()[:140]
    if not body.strip():
        return None
    return {
        "id": index,
        "title": title or url,
        "snippet": _snippet(body),
        "url": url,
        "publisher": _publisher(url, metadata),
        "date": str(metadata.get("published") or metadata.get("ts") or "")[:32],
        "collection": str(hit.get("collection") or ""),
        "score": float(hit.get("score") or hit.get("similarity") or 0.0),
    }


async def _default_search(
    query: str,
    *,
    collections: Sequence[str],
    limit: int,
    min_similarity: float,
) -> Sequence[dict[str, Any]]:
    from rugbrain.search.engine import search_multi_collection

    return await search_multi_collection(
        query=query,
        collections=list(collections),
        limit=limit,
        min_similarity=min_similarity,
    )


async def collect_evidence(
    topic: str,
    *,
    search_fn: SearchFn | None = None,
    collections: Sequence[str] = EVIDENCE_COLLECTIONS,
    limit: int = 10,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[dict[str, Any]]:
    """Retrieve citable evidence for ``topic``, de-duplicated by URL."""
    search = search_fn or _default_search
    hits = await search(
        topic, collections=collections, limit=limit, min_similarity=min_similarity
    )
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        row = normalise_hit(hit, len(evidence) + 1)
        if row is None or row["url"] in seen:
            continue
        seen.add(row["url"])
        evidence.append(row)
    return evidence


def sufficiency(
    evidence: Sequence[dict[str, Any]], min_sources: int = 2
) -> tuple[bool, str]:
    """Two independent publishers or no brief.

    Returns ``(ok, reason)``; the reason always names the count that failed so
    the operator can tell a thin corpus from a thin query.
    """
    if len(evidence) < min_sources:
        return False, (
            f"only {len(evidence)} citable source(s) with a URL; need {min_sources}"
        )
    domains = {
        urlparse(row["url"]).netloc.lower() for row in evidence if row.get("url")
    }
    if len(domains) < min_sources:
        listed = ", ".join(sorted(domains)) or "none"
        return False, (
            f"{len(evidence)} source(s) but {len(domains)} independent publisher(s) "
            f"({listed}); need {min_sources}"
        )
    return True, f"{len(evidence)} sources from {len(domains)} publishers"
