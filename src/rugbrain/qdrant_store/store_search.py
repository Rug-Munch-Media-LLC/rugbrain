"""Dense semantic search and native RRF hybrid search mixin."""

from __future__ import annotations

from typing import Any

from rugbrain.qdrant_store import _types


class SearchMixin(_types.StoreSurface):
    """Dense ``search`` + native Qdrant RRF fusion helper."""

    async def search(
        self,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.6,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search with optional payload filters.

        ``filters`` may be either:
          - a plain ``{field: value}`` dict (legacy exact-match form), or
          - a structured ``{"match": {...}, "range": [...]}`` dict as produced
            by ``build_search_filters``.
        """
        import logging

        import rugbrain.qdrant_store as _pkg

        logger = logging.getLogger(__name__)
        collection, unified_condition = _pkg.unified_route(collection)
        coll = collection or "default"
        client = await self._get_client()
        await _pkg._ensure_collection(coll, len(query_embedding), client)

        # Hybrid collections (created by scripts/migrate_to_sparse.py) and
        # named dense-only collections (e.g. news_v2) carry named vectors.
        # A bare vector is rejected by Qdrant against a named-vector
        # collection ("Not existing vector name"), so send the named dense
        # vector when the collection uses named vectors. Dense-only legacy
        # collections keep using the bare vector.
        vector_field: Any = query_embedding
        if await self._collection_uses_named_vectors(coll, client):
            vector_field = {"name": _pkg.DENSE_VECTOR_NAME, "vector": query_embedding}

        payload: dict[str, Any] = {
            "vector": vector_field,
            "limit": limit,
            "score_threshold": min_similarity,
            "with_payload": True,
        }
        if filters or unified_condition:
            qdrant_filter = _pkg.normalize_filters(filters)
            if unified_condition:
                qdrant_filter = qdrant_filter or {"must": []}
                qdrant_filter.setdefault("must", []).append(unified_condition)
            _pkg.apply_payload_filter(payload, qdrant_filter)

        try:
            r = await client.post(f"/collections/{coll}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning("qdrant_search_failed status=%d body=%s", r.status_code, r.text)
                return []
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "similarity": round(point.get("score", 0), 4),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            return results
        except Exception as e:
            logger.warning("qdrant_search_error: %s", e)
            return []

    async def _hybrid_search_rrf(
        self,
        req: _types.HybridRrfQuery,
    ) -> list[dict[str, Any]] | None:
        """Native Qdrant hybrid search: named dense + BM25 sparse, RRF fusion.

        Returns formatted results, or None when Qdrant rejects the request
        so the caller can fall back to dense-only search.
        """
        import logging

        import rugbrain.qdrant_store as _pkg

        logger = logging.getLogger(__name__)
        client = await self._get_client()
        payload: dict[str, Any] = {
            "vector": {"name": _pkg.DENSE_VECTOR_NAME, "vector": req.dense_vector},
            "sparse_vector": {
                "name": _pkg.SPARSE_VECTOR_NAME,
                "vector": {"indices": req.sparse[0], "values": req.sparse[1]},
            },
            "fusion": "rrf",
            "limit": req.limit,
            "with_payload": True,
        }
        _pkg.apply_payload_filter(payload, req.qdrant_filter)
        try:
            r = await client.post(f"/collections/{req.collection}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning(
                    "qdrant_hybrid_rrf_failed status=%d body=%s",
                    r.status_code,
                    r.text,
                )
                return None
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "similarity": round(point.get("score", 0), 4),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            return results
        except Exception as e:
            logger.warning("qdrant_hybrid_rrf_error: %s", e)
            return None
