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


class RerankUnavailableError(RuntimeError):
    """The rerank stage could not be APPLIED, so the layer is not a rerank.

    Raised instead of degrading. The whole point of a per-layer ratchet is to
    tell rerank apart from hybrid; a layer that quietly reports the hybrid
    order under the name "rerank" is worse than a missing layer, because it
    makes the gate report agreement where the system is untested. The runner
    counts this as an error, so a systematic failure lands as
    ``measured=0`` -> ``could not measure``, never as a passing number.
    """


def rerank_method_of(hits: list[dict[str, Any]]) -> str:
    """The rerank method actually applied to *hits*, or ``"none"``."""
    methods = {h.get("rerank_method") for h in hits if isinstance(h, dict)}
    methods.discard(None)
    if not methods:
        return "none"
    if len(methods) == 1:
        return str(next(iter(methods)))
    return "mixed:" + ",".join(sorted(str(m) for m in methods))


async def _rerank(query: str, hits: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Apply the rerank stage to a candidate pool, or raise.

    The candidate function is ``search.rerank.rerank_chunks`` — there is no
    ``rerank`` symbol in that module, and the ratchet used to call
    ``rerank_mod.rerank(...)``. That raised ``AttributeError`` on every query,
    which the bare ``except Exception: return hits`` below turned into "return
    the hybrid order unchanged". The rerank layer was therefore a byte-for-byte
    copy of the hybrid layer, and a 42-query baseline recorded both.

    Two further faults were hiding behind it:

    * ``rerank_chunks`` defaults to ``keep=5``. Calling it on a 40-document
      pool with the default returned 5 documents, which the length guard
      rejected as a shape mismatch — indistinguishable from a no-op. Both
      ``top_n`` and ``keep`` are now pinned to the pool size.
    * the module's own no-op path (``rerank_method="none"``, produced when
      every rung is unreachable) is a valid return in production, and was
      previously indistinguishable from a real rerank here. It is now an
      error, so an unreachable reranker cannot be reported as a passing
      layer.
    """
    from rugbrain.search import rerank as rerank_mod

    if not rerank_mod.RERANKER_ENABLED:
        raise RerankUnavailableError("reranker disabled (RERANKER_ENABLED=false)")

    pool = list(hits[:cap])
    if not pool:
        raise RerankUnavailableError("empty candidate pool")

    try:
        # top_n == keep == len(pool): score the whole pool and return it whole.
        # The defaults (20/5) would silently truncate and then look like a
        # shape mismatch.
        out = await rerank_mod.rerank_chunks(query, pool, top_n=len(pool), keep=len(pool))
    except Exception as exc:
        # Re-raised, not swallowed: an unreachable reranker is a measurement
        # error, never a pass-through order.
        raise RerankUnavailableError(f"rerank call failed: {exc}") from exc

    if not isinstance(out, list):
        raise RerankUnavailableError(f"rerank returned {type(out).__name__}, not a list")
    if len(out) != len(pool):
        raise RerankUnavailableError(f"rerank returned {len(out)} docs, expected {len(pool)}")

    method = rerank_method_of(out)
    if method == "none" or not all(h.get("reranked") for h in out if isinstance(h, dict)):
        raise RerankUnavailableError(
            "rerank ran but applied no scoring (rerank_method=none); "
            "reranker returned the pre-rerank order unchanged"
        )
    return out
