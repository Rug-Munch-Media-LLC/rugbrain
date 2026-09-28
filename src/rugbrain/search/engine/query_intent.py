"""
RAG Search Engine — query intent detection and collection routing.

Scam/exploit vs smart-contract coding intent detection plus the effective
collection-list resolution used by the search entry points.
"""

from rugbrain.crypto_embeddings import COLLECTIONS
from rugbrain.search import lane_targets as _lane

from .constants import CODING_COLLECTIONS, SCAM_COLLECTIONS
from .hints import (
    _CODING_CTX_RE,
    _CODING_QUERY_HINTS,
    _CODING_TERM_RE,
    _HOW_DOES_RE,
    _SCAM_QUERY_HINTS,
)


def detect_scam_query(query: str) -> bool:
    """Return True when *query* looks like a scam/exploit knowledge query.

    Used to prioritize SCAM_COLLECTIONS in ``three_pillar_search`` /
    ``search_multi_collection`` when the caller did not pin collections.
    """
    q = (query or "").lower()
    if not q:
        return False
    return any(hint in q for hint in _SCAM_QUERY_HINTS)


def detect_coding_query(query: str) -> bool:
    """Return True when *query* looks like a smart-contract coding question.

    Code-intent queries ("how to implement X in Solidity", "Solana PDA
    security best practices", "secure ERC20", "what is the ABI of a
    function") are routed to CODING_COLLECTIONS first so RugBrain answers
    as a development expert.

    Two tiers: the phrase hints above, then word-boundary code-term regexes.
    Bare ambiguous terms (e.g. "reentrancy", "code") only count when paired
    with a second code term or a "how does" phrasing, so exploit-framed
    queries ("reentrancy hack 2023") keep routing to scam/exploit knowledge.
    """
    q = (query or "").lower()
    if not q:
        return False
    if any(hint in q for hint in _CODING_QUERY_HINTS):
        return True
    if _CODING_TERM_RE.search(q):
        return True
    ctx = list(_CODING_CTX_RE.finditer(q))
    if len(ctx) >= 2:
        return True
    return bool(ctx and _HOW_DOES_RE.search(q))


def resolve_search_collections(
    collections: list[str] | tuple[str, ...] | None,
    query: str | None = None,
) -> list[str]:
    """Resolve the effective collection list for a search.

    - Explicit ``collections`` is returned as-is (caller wins).
    - Otherwise, code-intent queries search CODING_COLLECTIONS first, then the
      default COLLECTIONS; scam/exploit queries search SCAM_COLLECTIONS first;
      general queries use only COLLECTIONS.

    LANE RESOLUTION IS NOT OPTIONAL. This is the function that builds the
    collection list for every orchestrated search (multi_collection,
    three_pillar, knowledge), so a base name leaving here reaches
    ``search_similar``/``query_points`` verbatim. The write path resolves the
    same names through ``lane_resolver.resolve_collection``; if the read side
    does not, the system ingests into a collection it cannot read. That is the
    ``qdrant_hybrid_search_blocked requested_dim=4096`` defect — correct in
    isolation (each name is a legal Qdrant collection), wrong in context, and
    therefore invisible. So the routing decision and the resolution happen in
    ONE place, and nothing downstream re-derives a name.

    Order is preserved: this list is a priority order, not a set.
    """
    if collections:
        return _lane.resolve_search_collections(collections)
    if query and detect_coding_query(query):
        return _lane.resolve_search_collections((*CODING_COLLECTIONS, *COLLECTIONS))
    if query and detect_scam_query(query):
        return _lane.resolve_search_collections((*SCAM_COLLECTIONS, *COLLECTIONS))
    return _lane.resolve_search_collections(COLLECTIONS)
