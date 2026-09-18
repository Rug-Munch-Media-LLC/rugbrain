"""
RAG Search Engine — query intent detection and collection routing.

Scam/exploit vs smart-contract coding intent detection plus the effective
collection-list resolution used by the search entry points.
"""

from rugbrain.crypto_embeddings import COLLECTIONS

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
    """
    if collections:
        return list(collections)
    if query and detect_coding_query(query):
        return [*CODING_COLLECTIONS, *COLLECTIONS]
    if query and detect_scam_query(query):
        return [*SCAM_COLLECTIONS, *COLLECTIONS]
    return list(COLLECTIONS)
