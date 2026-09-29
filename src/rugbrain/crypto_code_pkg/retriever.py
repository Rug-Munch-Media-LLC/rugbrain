"""The ContextCrypto retriever: dense search + keyword re-rank + RRF fusion.

One job: execute a hybrid search over the read-only ContextCrypto collections
and return attributed, fused, top-k results. Classification lives in
:mod:`classify`, attribution in :mod:`attribution`, and the public entry points
in ``rugbrain.crypto_code_retrieval``.

This talks to Qdrant REST directly, NOT through ``QdrantVectorStore``: the
store's result mapping drops the ContextCrypto payload fields
(``code_block``, ``library_id``, ...) that attribution needs.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.crypto_code_pkg.attribution import format_hit, keyword_score
from rugbrain.rrf_fusion import _rrf_fuse

logger = logging.getLogger(__name__)

CODEBASE_REFERENCE_COLLECTION = "codebase_reference"
SECURITY_PATTERNS_COLLECTION = "security_patterns"
CONTEXTCRYPTO_COLLECTIONS: tuple[str, ...] = (
    CODEBASE_REFERENCE_COLLECTION,
    SECURITY_PATTERNS_COLLECTION,
)

#: RRF smoothing constant, and its inline twin used when scoring a raw point.
_RRF_K = 60

#: Structured facets a caller may filter on; each is a payload field.
FILTER_FIELDS = ("verdict", "library_id", "category", "severity", "ecosystem")


async def _resolve_base_url() -> str:
    """Async resolve the Qdrant base URL via the shared fallback chain."""
    from rugbrain.qdrant_store import _resolve_qdrant_url

    return await _resolve_qdrant_url()


def build_qdrant_filter(**facets: str | None) -> dict[str, Any] | None:
    """Build a match-any Qdrant filter from the non-empty *facets*."""
    match_fields = {f: v for f, v in facets.items() if v}
    return {"match": match_fields} if match_fields else None


def _rank_points(query: str, points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score points by keyword overlap, best first."""
    return sorted(
        (
            {
                "id": p.get("id"),
                "keyword_score": keyword_score(query, p.get("payload") or {}),
            }
            for p in points
        ),
        key=lambda x: x["keyword_score"],
        reverse=True,
    )


class CryptoCodeRetriever:
    """Hybrid retriever over the ContextCrypto collections."""

    def __init__(
        self,
        embedder: Any | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._embedder = embedder
        self._base_url = base_url
        self._timeout = timeout

    async def _get_embedder(self) -> Any:
        if self._embedder is None:
            from rugbrain.embedder import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    async def _get_base_url(self) -> str:
        if self._base_url is None:
            self._base_url = await _resolve_base_url()
        return self._base_url

    async def _search_collection(
        self,
        client: httpx.AsyncClient,
        collection: str,
        query_embedding: list[float],
        limit: int,
        qdrant_filter: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Dense search one collection; returns raw points (empty on failure)."""
        payload: dict[str, Any] = {
            "vector": query_embedding,
            "limit": limit,
            "with_payload": True,
            "with_vectors": False,
        }
        if qdrant_filter:
            payload["filter"] = qdrant_filter
        try:
            r = await client.post(f"/collections/{collection}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning(
                    "crypto_code_search_failed collection=%s status=%d body=%s",
                    collection,
                    r.status_code,
                    r.text[:200],
                )
                return []
            return list(r.json().get("result", []))
        except Exception as e:
            logger.warning("crypto_code_search_error collection=%s err=%s", collection, e)
            return []

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        verdict: str | None = None,
        library_id: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        ecosystem: str | None = None,
    ) -> dict[str, Any]:
        """Hybrid crypto-code retrieval with source attribution.

        Dense search both ContextCrypto collections, keyword re-score the
        candidates, RRF-fuse the two rankings, return the top-k attributed
        results.
        """
        if not (query or "").strip():
            return {"query": query, "results": [], "total": 0, "collections": []}

        embedder = await self._get_embedder()
        query_embedding = await embedder.embed_one(query)
        qdrant_filter = build_qdrant_filter(
            verdict=verdict,
            library_id=library_id,
            category=category,
            severity=severity,
            ecosystem=ecosystem,
        )
        base_url = await self._get_base_url()

        raw_by_collection, keyword_lists = await self._search_all(
            base_url, query, query_embedding, top_k, qdrant_filter
        )
        return self._fuse(query, raw_by_collection, keyword_lists, top_k)

    async def _search_all(
        self,
        base_url: str,
        query: str,
        query_embedding: list[float],
        top_k: int,
        qdrant_filter: dict[str, Any] | None,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[list[dict[str, Any]]]]:
        """Dense+keyword rank every collection, in one pooled connection."""
        raw_by_collection: dict[str, list[dict[str, Any]]] = {}
        keyword_lists: list[list[dict[str, Any]]] = []
        async with httpx.AsyncClient(base_url=base_url, timeout=self._timeout) as client:
            for collection in CONTEXTCRYPTO_COLLECTIONS:
                points = await self._search_collection(
                    client, collection, query_embedding, top_k * 2, qdrant_filter
                )
                raw_by_collection[collection] = points
                keyword_lists.append(_rank_points(query, points))
        return raw_by_collection, keyword_lists

    def _fuse(
        self,
        query: str,
        raw_by_collection: dict[str, list[dict[str, Any]]],
        keyword_lists: list[list[dict[str, Any]]],
        top_k: int,
    ) -> dict[str, Any]:
        """RRF-fuse the dense and keyword rankings, then attribute and cut."""
        dense_lists = [
            [{"id": p.get("id"), "dense_score": p.get("score", 0)} for p in points]
            for points in raw_by_collection.values()
        ]
        fused = _rrf_fuse([*dense_lists, *keyword_lists], k=_RRF_K)
        score_by_id = {f["id"]: f["rrf_score"] for f in fused}

        results: list[dict[str, Any]] = []
        for index, (collection, points) in enumerate(raw_by_collection.items()):
            dense_rank = {p.get("id"): i for i, p in enumerate(points)}
            kw_by_id = {k["id"]: k["keyword_score"] for k in keyword_lists[index]}
            for p in points:
                pid = p.get("id")
                results.append(
                    format_hit(
                        p,
                        collection,
                        dense_score=1.0 / (_RRF_K + dense_rank.get(pid, _RRF_K) + 1),
                        keyword_score_value=kw_by_id.get(pid, 0.0),
                    )
                )
        for r in results:
            r["rrf_score"] = score_by_id.get(r["id"], 0.0)
        results.sort(key=lambda x: x["rrf_score"], reverse=True)
        return {
            "query": query,
            "collections": list(CONTEXTCRYPTO_COLLECTIONS),
            "results": results[:top_k],
            "total": min(len(results), top_k),
            "retriever": "contextcrypto-hybrid",
        }
