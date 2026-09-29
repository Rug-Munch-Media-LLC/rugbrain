"""Public entry points for the crypto-code path.

One job: gate and dispatch. Decide whether a caller is allowed raw ContextCrypto
results, classify the query, and hand off to
:class:`~rugbrain.crypto_code_pkg.retriever.CryptoCodeRetriever`. The retrieval
itself lives in :mod:`retriever`, classification in :mod:`classify`, and the
result projection in :mod:`attribution`.

This module is a stable facade: ``CryptoCodeRetriever``,
``classify_crypto_code_query``, and ``search_crypto_code`` are imported from
here by the route tests and by ``search.engine.knowledge``.
"""

from __future__ import annotations

import logging

from rugbrain.crypto_code_pkg.attribution import VERDICT_LABELS, augment_hit
from rugbrain.crypto_code_pkg.classify import classify_crypto_code_query
from rugbrain.crypto_code_pkg.retriever import (
    CODEBASE_REFERENCE_COLLECTION,
    CONTEXTCRYPTO_COLLECTIONS,
    SECURITY_PATTERNS_COLLECTION,
    CryptoCodeRetriever,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CODEBASE_REFERENCE_COLLECTION",
    "CONTEXTCRYPTO_COLLECTIONS",
    "SECURITY_PATTERNS_COLLECTION",
    "VERDICT_LABELS",
    "CryptoCodeRetriever",
    "augment_coding_results",
    "classify_crypto_code_query",
    "get_crypto_code_retriever",
    "search_crypto_code",
]

_retriever: CryptoCodeRetriever | None = None


def get_crypto_code_retriever() -> CryptoCodeRetriever:
    """Process-wide retriever singleton."""
    global _retriever
    if _retriever is None:
        _retriever = CryptoCodeRetriever()
    return _retriever


async def search_crypto_code(
    query: str,
    top_k: int = 10,
    verdict: str | None = None,
    library_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    ecosystem: str | None = None,
    force: bool = False,
    *,
    audience: str = "internal",
) -> dict:
    """Internal entry point: classify, then retrieve (or report not-routed)."""
    if audience != "internal":
        return {
            "query": query,
            "routed": False,
            "results": [],
            "total": 0,
            "note": "raw code retrieval is internal-only",
        }
    routed = force or classify_crypto_code_query(query)
    if not routed:
        return {
            "query": query,
            "routed": False,
            "results": [],
            "total": 0,
            "note": "query not classified as crypto-code; general RAG unchanged",
        }
    result = await get_crypto_code_retriever().retrieve(
        query,
        top_k=top_k,
        verdict=verdict,
        library_id=library_id,
        category=category,
        severity=severity,
        ecosystem=ecosystem,
    )
    result["routed"] = True
    return result


# ── Route-logic hook for the coding path ──────────────────────────────


async def augment_coding_results(
    query: str,
    results: list[dict],
    limit: int = 5,
    *,
    audience: str = "public",
) -> list[dict]:
    """Optionally merge raw ContextCrypto hits for an internal caller.

    Public coding search must not receive raw ContextCrypto code or source
    coordinates. The default is therefore fail-closed: callers must opt in to
    ``audience="internal"`` after authentication and policy review.
    """
    if audience != "internal" or not classify_crypto_code_query(query):
        return results
    try:
        crypto = await search_crypto_code(query, top_k=limit, audience="internal")
    except Exception as e:
        logger.warning("crypto_code_augment_failed err=%s", e)
        return results
    merged = list(results)
    seen = {r.get("id") for r in merged if r.get("id")}
    for r in crypto.get("results", []):
        if r.get("id") in seen:
            continue
        merged.append(augment_hit(r))
    merged.sort(key=lambda x: x.get("rrf_score", x.get("similarity", 0)), reverse=True)
    return merged
