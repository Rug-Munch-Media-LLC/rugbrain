"""HTTP + result-shape helpers for the async market-specialist surface."""

from __future__ import annotations

from typing import Any

import httpx

from rugbrain.rugbrain_mcp.app import BASE_URL, HEADERS, MultiSearchRequest


async def _call_search_multi(
    query: str,
    collections: list[str],
    limit: int = 10,
    limit_per_collection: int = 20,
    rerank: bool = False,
) -> dict[str, Any]:
    """Call the RugBrain /api/v1/search/multi endpoint."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BASE_URL}/api/v1/search/multi",
            json=MultiSearchRequest(
                query=query,
                collections=collections,
                limit=limit,
                limit_per_collection=limit_per_collection,
                rerank=rerank,
            ).model_dump(),
            headers=HEADERS,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()


def _extract_results(resp: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    """Normalize raw API search results into a uniform item shape.

    The API does not emit a ``score`` key: a reranked hit carries
    ``rerank_score``, a fused hit ``rrf_score``, and a raw hit ``similarity``.
    Reading only ``score`` made every item 0.0, so the caller's sort was a
    no-op and the brief was effectively unordered.
    """
    out: list[dict[str, Any]] = []
    for item in resp.get("results", []):
        score = item.get("rerank_score")
        if score is None:
            score = item.get("rrf_score")
        if score is None:
            score = item.get("similarity", 0.0)
        out.append(
            {
                "content": item.get("content", ""),
                "collection": collection,
                "score": float(score or 0.0),
            }
        )
    return out


def _source_of(item: dict[str, Any]) -> str:
    """The collection an item came from.

    The API tags fused hits with ``_source_collections`` (a list, because RRF
    can fuse the same doc from several); the older shape used ``collection``.
    """
    sources = item.get("_source_collections")
    if isinstance(sources, list) and sources:
        return str(sources[0])
    single = item.get("collection")
    return str(single) if isinstance(single, str) else "?"
