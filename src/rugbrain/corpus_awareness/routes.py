"""Question -> collection routing over the repo's DECLARED tables.

The scam/coding tables are imported from the search engine. When that import
is unavailable we route nothing (`_DECLARED is False`) instead of inventing a
mapping — a fabricated route silently answers the wrong corpus.
"""

from __future__ import annotations

NEWS_COLLECTIONS: tuple[str, ...] = ("news_v2",)

try:
    from rugbrain.search.engine.constants import CODING_COLLECTIONS, SCAM_COLLECTIONS

    _DECLARED = True
except Exception:  # noqa: BLE001 - optional dependency of this read-only view
    CODING_COLLECTIONS = ()
    SCAM_COLLECTIONS = ()
    _DECLARED = False

_HINTS: dict[str, tuple[str, ...]] = {
    "exploit": ("exploit", "hack", "rug", "scam", "vulnerab", "reentran", "drain", "audit"),
    "code": ("how to", "implement", "solidity", "rust", "anchor", "move", "cairo", "pattern"),
    "news": ("news", "headline", "announce", "launch", "today", "this week"),
    "repo": ("our repo", "our own", "rmi-backend", "rugbrain", "internal", "codebase"),
}
_REPO_MARKERS = ("internal", "repo", "codebase")


def _hits(question: str, bucket: str) -> bool:
    return any(hint in question for hint in _HINTS[bucket])


def _repo_collections(available: list[str] | None) -> list[str]:
    if not available:
        return []
    return [n for n in available if any(m in n for m in _REPO_MARKERS)]


def route_question(question: str, available: list[str] | None = None) -> list[str]:
    """Collections to search for *question*, intent-first and deduped.

    Filtered against *available* (the live list) when the caller knows it, so
    a route never points at a collection that no longer exists.
    """
    q = (question or "").lower()
    order: list[str] = []

    def add(names) -> None:
        for name in names:
            if name not in order:
                order.append(name)

    if _hits(q, "exploit"):
        add(SCAM_COLLECTIONS)
    if _hits(q, "code"):
        add(CODING_COLLECTIONS)
    if _hits(q, "repo"):
        add(_repo_collections(available))
    if _hits(q, "news"):
        add(NEWS_COLLECTIONS)
    if not order:
        add(SCAM_COLLECTIONS)
        add(NEWS_COLLECTIONS)
    if available:
        order = [n for n in order if n in available]
    return order


def exposed_by(name: str) -> str:
    """Which MCP tool / route serves this collection, or an explicit no-dedicated."""
    if name in NEWS_COLLECTIONS:
        return "rugbrain_news + /api/v1/search"
    if name in SCAM_COLLECTIONS:
        return "rugbrain_search + /api/v1/search (scam pillar)"
    if name in CODING_COLLECTIONS:
        return "rugbrain_search_multi (coding pillar)"
    if any(m in name for m in _REPO_MARKERS):
        return "rugbrain_search_multi (scoped collection)"
    return "rugbrain_search_multi (default route, no dedicated tool)"
