"""Retriever construction for the retrieval ratchet layers.

Split from `ratchet_runner` on 2026-09-28: dense, hybrid and reranked retrievers
are infrastructure wiring, not measurement policy.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

def _make_dense_retriever(store: Any, limit: int) -> Any:
    async def _dense(query: str, collection: str) -> list[dict[str, Any]]:
        from rugbrain.embeddings import get_query_embedding

        vector = await get_query_embedding(query)
        # store.search is (query_embedding, collection, ...). Passing the
        # collection positionally FIRST put a 4096-float list where the
        # collection name is read, so resolve_collection() called .endswith()
        # on a list and the whole dense layer died as
        # "'list' object has no attribute 'endswith'". Every other caller in
        # the repo passes the embedding first; this was the one that did not.
        # Keyword args now, so the order cannot silently invert again.
        return await store.search(query_embedding=vector, collection=collection, limit=limit)

    return _dense


def _make_hybrid_retriever(store: Any, limit: int) -> Any:
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


async def _rerank(query: str, hits: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Apply the rerank stage to a candidate pool, tolerating its absence.

    Rerank is best-effort by design: ``search/rerank.py`` returns documents in
    their original order when the reranker is unreachable. That is correct
    production behaviour but it would silently make the rerank layer a copy of
    hybrid, so the applied method is reported and an absent rerank is visible
    in the measurement rather than hidden.
    """
    from rugbrain.search import rerank as rerank_mod

    if not rerank_mod.RERANKER_ENABLED:
        return hits
    try:
        out = await rerank_mod.rerank(query, list(hits[:cap]))
    except Exception:
        return hits
    if not isinstance(out, list) or len(out) != len(hits[:cap]):
        # A length-mismatched order is discarded in silence downstream and
        # looks exactly like "rerank ran and did nothing".
        return hits
    return out
