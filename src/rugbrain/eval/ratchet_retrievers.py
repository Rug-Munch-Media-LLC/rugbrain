"""Retriever factories for the retrieval ratchet — one per measured layer.

Split out of ``ratchet_runner`` so the store wiring is separate from the run
loop. Each layer is built here and the loop stays ignorant of how a layer
retrieves, so adding a layer does not mean editing the loop.

    dense   ``store.search`` — semantic only.
    hybrid  ``store.hybrid_search`` — dense + BM25 sparse with native RRF
            fusion. This is what production serves, so this is what the gate
            decides on.
    rerank  hybrid, then the rerank stage reorders the candidate pool.
            Reordering changes which documents survive truncation, so a corpus
            that passes hybrid can still fail rerank. Both are measured; only
            hybrid gates.

Imports are deferred inside the closures so that merely importing this module
does not pull in the embedder or the reranker — the nightly must stay runnable
on a host where either is absent.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def make_dense_retriever(store: Any, limit: int) -> Any:
    async def _dense(query: str, collection: str) -> list[dict[str, Any]]:
        from rugbrain.embeddings import get_query_embedding

        vector = await get_query_embedding(query)
        return await store.search(collection, vector, limit=limit)

    return _dense


def make_hybrid_retriever(store: Any, limit: int) -> Any:
    async def _hybrid(query: str, collection: str) -> list[dict[str, Any]]:
        from rugbrain.embeddings import get_query_embedding

        vector = await get_query_embedding(query)
        return await store.hybrid_search(
            query_text=query,
            query_embedding=vector,
            collection=collection,
            limit=limit,
        )

    return _hybrid


async def rerank_hits(query: str, hits: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Apply the rerank stage to a candidate pool, tolerating its absence.

    Rerank is best-effort by design: ``search/rerank.py`` returns documents in
    their original order when the reranker is unreachable. That is correct
    production behaviour but it would silently make the rerank layer a copy of
    hybrid, so a length-mismatched order is DISCARDED and reported rather than
    passed on — otherwise a reordered list is silently truncated back to the
    hybrid order downstream and looks exactly like "rerank ran and did
    nothing".
    """
    from rugbrain.search import rerank as rerank_mod

    if not rerank_mod.RERANKER_ENABLED:
        return hits
    try:
        out = await rerank_mod.rerank(query, list(hits[:cap]))
    except Exception:
        return hits
    if not isinstance(out, list) or len(out) != len(hits[:cap]):
        return hits
    return out


__all__ = ["make_dense_retriever", "make_hybrid_retriever", "rerank_hits"]
