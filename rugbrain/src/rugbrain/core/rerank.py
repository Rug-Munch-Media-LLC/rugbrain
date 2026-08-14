"""Infinity cross-encoder reranking for the RAG pipeline.

Reranks retrieved candidates with Infinity's ``/rerank`` endpoint
(model ``BAAI/bge-reranker-v2-m3``) before they are handed to the LLM.

Fail-open: if Infinity is unreachable, callers keep the pre-rerank order
so search never breaks because the reranker is unavailable.
"""

from __future__ import annotations

from typing import Any

import httpx

from rugbrain._logging import get_logger
from rugbrain.observability.tracing import trace_span
from rugbrain.settings import settings

log = get_logger(__name__)

INFINITY_RERANK_MODEL: str = "BAAI/bge-reranker-v2-m3"


async def rerank_results(query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
    """Rerank *documents* against *query* via Infinity's cross-encoder.

    Args:
        query: The user query string.
        documents: Candidate document texts (same order as retrieval).
        top_n: Maximum number of ranked results to return.

    Returns:
        List of ``{"index": int, "relevance_score": float}`` dicts ordered
        by relevance descending. Only documents with
        ``relevance_score > 0.0`` are kept (per spec). Empty list on
        failure (fail-open) or when nothing clears the threshold.
    """
    if not query or not documents:
        return []
    with trace_span(
        "rag.rerank.infinity",
        {
            "openinference.span.kind": "RERANKER",
            "openinference.embedding.model": INFINITY_RERANK_MODEL,
            "rerank.input_count": len(documents),
            "rerank.top_n": top_n,
        },
    ) as span:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.infinity_url.rstrip('/')}/rerank",
                    json={
                        "model": INFINITY_RERANK_MODEL,
                        "query": query,
                        "documents": documents,
                        "top_n": min(top_n, len(documents)),
                    },
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            # Filter: only keep docs with relevance_score > 0.0
            kept = [r for r in results if r.get("relevance_score", 0.0) > 0.0]
            span.set_attribute("rerank.result_count", len(kept))
            return kept
        except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
            log.debug("rerank_results_unavailable err=%s", exc)
            return []


def reorder_by_relevance(items: list[Any], reranked: list[dict[str, Any]], top_n: int) -> list[Any]:
    """Reorder *items* (index-aligned with the rerank request) by Infinity
    relevance, mutating each item's ``final_score`` and
    ``metadata["rerank_score"]``.

    Args:
        items: Retrieved hit objects (duck-typed: need ``final_score`` and
            ``metadata`` attributes).
        reranked: Output of :func:`rerank_results` (``index`` + ``relevance_score``).
        top_n: Truncate the reordered list to this many items.

    Returns:
        Reordered items (``[]`` when nothing survived the ``> 0.0`` filter).
    """
    ordered: list[Any] = []
    for item in sorted(reranked, key=lambda x: -x["relevance_score"]):
        idx = item["index"]
        if 0 <= idx < len(items):
            hit = items[idx]
            score = float(item["relevance_score"])
            hit.final_score = score
            hit.metadata = {**hit.metadata, "rerank_score": round(score, 4)}
            ordered.append(hit)
    return ordered[:top_n]


__all__ = [
    "INFINITY_RERANK_MODEL",
    "reorder_by_relevance",
    "rerank_results",
]
