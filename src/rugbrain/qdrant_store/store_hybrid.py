"""Hybrid (dense + BM25 sparse) search with keyword re-rank fallback."""

from __future__ import annotations

from typing import Any

from rugbrain.qdrant_store import _types


class HybridSearchMixin(_types.StoreSurface):
    """``hybrid_search`` — RRF fusion when available, dense fallback otherwise."""

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        vector_weight: float = 0.7,
        min_similarity: float = 0.6,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Dense + BM25 sparse hybrid search with Reciprocal Rank Fusion.

        When the target collection carries the ``bm25`` sparse vector
        (post-migration) and the BM25 embedder is available, Qdrant fuses
        the dense and sparse result lists natively (``fusion="rrf"``).
        Otherwise it falls back to the dense-only search with lightweight
        keyword re-ranking so pre-migration collections keep working.
        collection = _map_collection(collection)

        ``filters`` uses the same structured shape as ``search``
        (``{"match": {...}, "range": [...]}``) and is applied to both the RRF
        request and the dense fallback.
        """
        import rugbrain.qdrant_store as _pkg

        coll, unified_condition = _pkg.unified_route(collection or "default")
        client = await self._get_client()
        await _pkg._ensure_collection(coll, len(query_embedding), client)

        qdrant_filter = _pkg.normalize_filters(filters)
        if unified_condition:
            qdrant_filter = qdrant_filter or {"must": []}
            qdrant_filter.setdefault("must", []).append(unified_condition)

        if query_text and await self._collection_supports_sparse(coll, client):
            sparse = await _pkg._embed_sparse(query_text)
            if sparse is not None:
                fused = await self._hybrid_search_rrf(
                    _types.HybridRrfQuery(
                        collection=coll,
                        dense_vector=query_embedding,
                        sparse=sparse,
                        limit=limit,
                        qdrant_filter=qdrant_filter,
                    )
                )
                if fused is not None:
                    return fused

        # ── Fallback: dense search + keyword re-rank (pre-sparse behaviour) ──
        results = await self.search(
            query_embedding,
            collection=coll,
            limit=limit * 2,
            min_similarity=min_similarity,
            filters=qdrant_filter,
        )

        if query_text and results:
            terms = set(query_text.lower().split())
            for r in results:
                content_lower = r.get("content", "").lower()
                kw_hits = sum(1 for t in terms if t in content_lower)
                keyword_score = kw_hits / max(1, len(terms))
                r["rrf_score"] = (
                    vector_weight * r["similarity"] + (1 - vector_weight) * keyword_score
                )
                r["match_type"] = "hybrid" if kw_hits > 0 else "vector"
            results.sort(
                key=lambda x: x.get("rrf_score", 0),
                reverse=True,
            )
        else:
            for r in results:
                r["rrf_score"] = r["similarity"]
                r["match_type"] = "vector"

        final: list[dict[str, Any]] = []
        for r in results[:limit]:
            r.pop("rrf_score", None)
            r.pop("match_type", None)
            final.append(r)
        return final
