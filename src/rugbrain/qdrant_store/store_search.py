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
        allow_create: bool = False,
    ) -> list[dict[str, Any]]:
        """Semantic search with optional payload filters.

        ``filters`` may be either:
          - a plain ``{field: value}`` dict (legacy exact-match form), or
          - a structured ``{"match": {...}, "range": [...]}`` dict as produced
            by ``build_search_filters``.


        Reads FAIL CLOSED by default: a search against an unknown collection
        name returns empty rather than materializing one. A read must never be
        the thing that forks a corpus. Pass ``allow_create=True`` explicitly
        for a deliberate provisioning read; the width is still checked against
        the registry's canonical lane, so it cannot create a retired-width
        fork.

        """
        import logging

        import rugbrain.qdrant_store as _pkg

        logger = logging.getLogger(__name__)
        collection, unified_condition = _pkg.unified_route(collection)
        coll = collection or "default"
        client = await self._get_client()
        if not await _pkg._ensure_collection(
            coll,
            len(query_embedding),
            client,
            create_if_missing=allow_create,
        ):
            logger.error(
                "qdrant_search_blocked collection=%s requested_dim=%d reason=incompatible_schema",
                coll,
                len(query_embedding),
            )
            return []

        # Hybrid collections and named dense-only collections require the
        # configured dense vector name. Do not assume it is always "dense";
        # Qdrant permits arbitrary named-vector keys.
        vector_field: Any = query_embedding
        if await self._collection_uses_named_vectors(coll, client):
            dense_name = await self._get_dense_vector_name(coll, client, len(query_embedding))
            if dense_name is None:
                logger.error(
                    "qdrant_search_blocked collection=%s reason=missing_dense_vector_name",
                    coll,
                )
                return []
            vector_field = {"name": dense_name, "vector": query_embedding}

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
        if req.sparse_name is None:
            return None
        client = await self._get_client()
        payload: dict[str, Any] = {
            "vector": (
                {"name": req.dense_name, "vector": req.dense_vector}
                if req.dense_name is not None
                else req.dense_vector
            ),
            "sparse_vector": {
                "name": req.sparse_name,
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
