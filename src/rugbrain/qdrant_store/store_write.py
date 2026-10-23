"""Write path: deterministic point IDs, dedupe check, gated insert, close."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from rugbrain.qdrant_store import _types
from rugbrain.qdrant_store import create_policy as _create_policy

logger = logging.getLogger(__name__)


class WriteMixin(_types.StoreSurface):
    """``insert`` / ``has_point`` / ``_to_uuid`` / ``close``."""

    _client: httpx.AsyncClient | None

    @staticmethod
    def _to_uuid(doc_id: str) -> str:
        """Convert a string doc_id to a deterministic UUID v5.

        Qdrant REST API requires point IDs to be unsigned integers or UUIDs.
        We use UUID v5 (SHA-1 namespace + name) for deterministic conversion.
        Namespace: 6ba7b810-9dad-11d1-80b4-00c04fd430c8 (DNS namespace)
        """
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))

    async def has_point(self, doc_id: str, collection: str | None) -> bool:
        """Return True when a deterministic point for *doc_id* already exists.

        Used by the shared ingest path (``ingest_common.store_documents``) to
        skip re-embedding content that is already stored — every pipeline that
        feeds the embedder goes through this check before calling Qwen3-8B, so
        re-running a crawl/cron does NOT re-embed the same chunks. Fail-open:
        any error (Qdrant down, malformed id) returns False so the caller
        proceeds to embed rather than dropping fresh content.
        """
        import rugbrain.qdrant_store as _pkg

        try:
            client = await self._get_client()
            collection = _pkg._map_collection(collection)
            point_id = self._to_uuid(doc_id)
            r = await client.get(f"/collections/{collection}/points/{point_id}")
            # 200 = exists; 404 = not found; anything else -> treat as exists-unknown (False)
            return bool(r.status_code == 200)
        except Exception as e:
            logger.debug("qdrant_has_point_error doc=%s err=%s", doc_id, str(e)[:120])
            return False

    async def insert(
        self,
        doc_id: str,
        collection: str | None,
        embedding: list[float],
        content: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
        severity: str = "medium",
        chain: str = "",
    ) -> bool:
        """Insert a document into *collection*.

        Named-vector collections (named dense, or dense + ``bm25`` hybrid) store
        the dense vector under its configured name; hybrid collections also get
        a BM25 sparse vector computed from *content* so RRF search can use it.
        Legacy bare ``{"size": ...}`` collections are written unchanged.
        """
        import rugbrain.qdrant_store as _pkg

        # The DECLARED name is the provenance readers filter on, captured before
        # any canonicalisation rewrites it to the lane twin.
        declared = collection
        collection = _pkg._map_collection(collection) or "default"
        # Write gate: refuse writes to non-owned collections in readonly mode.
        # Ownership is evaluated on the CANONICAL name — neither the ``_8b``
        # lane suffix nor the interim suffix denotes a different owner, so
        # gate_collection_name strips both before the owned-set lookup.
        gate_name = _pkg.gate_collection_name(collection, _pkg._INTERIM_SUFFIX)
        if (
            _pkg._RMI_RAG_QDRANT_WRITE_MODE == "readonly"
            and gate_name not in _pkg._RMI_RAG_OWNED_COLLECTIONS
        ):
            logger.warning(
                "qdrant_write_gate_blocked collection=%s mode=readonly owned_by=rmi-backend",
                collection,
            )
            return False

        # Resolve the LANE from the vector's actual width before the schema
        # check. The resolver rewrites a base collection to its `_8b` twin, but
        # the twin is a different dimension (threat_intel_v2 2560 vs
        # threat_intel_v2_8b 4096), so a 2560 vector routed to the 4096 lane was
        # rejected and the write DROPPED SILENTLY. A dim mismatch must never
        # look like a successful ingest.
        from rugbrain.qdrant_store.write_lane import write_lane_for_dim

        # Stamping the RESOLVED name here hid every lane-written point from
        # unified reads: 1,780 X points carried ``collection: news_v2_8b`` while
        # ``RAG_UNIFIED_READS=1`` filtered ``collection == news_v2``.
        collection = write_lane_for_dim(collection, len(embedding))

        client = await self._get_client()
        # CREATION IS AN EXPLICIT, NAMED ACT HERE. The old call omitted
        # ``create_if_missing`` and inherited a ``True`` default, so a typo'd
        # collection name was silently CREATED at the vector's width and the
        # write SUCCEEDED — forking a fresh empty corpus. The write path
        # genuinely needs to be able to provision a canonical collection (app
        # bootstrap / first ingest of a declared target), so it names the
        # opt-in explicitly; the width check then still refuses any
        # non-canonical (retired) width, raising rather than forking.
        try:
            ok = await _pkg._ensure_collection(
                collection, len(embedding), client, create_if_missing=True
            )
        except _create_policy.RetiredWidthCreationRefused as exc:
            logger.error("qdrant_insert_blocked collection=%s reason=%s", collection, exc)
            return False
        if not ok:
            logger.error(
                "qdrant_insert_blocked collection=%s requested_dim=%d reason=incompatible_schema",
                collection,
                len(embedding),
            )
            return False

        point_id = self._to_uuid(doc_id)
        vector_field = await self._resolve_vector_field(collection, embedding, content, client)
        if vector_field is None:
            return False
        payload = {
            "points": [
                {
                    "id": point_id,
                    "vector": vector_field,
                    "payload": {
                        "content": content[:10000],
                        "metadata": metadata or {},
                        "source": source,
                        "severity": severity,
                        "chain": chain,
                        "collection": declared or collection,
                        "lane_collection": collection,
                    },
                }
            ]
        }
        try:
            r = await client.put(f"/collections/{collection}/points", json=payload)
            ok = r.status_code in (200, 201)
            if not ok:
                logger.warning(
                    "qdrant_insert_failed status=%d body=%s",
                    r.status_code,
                    r.text,
                )
            return ok
        except Exception as e:
            logger.warning("qdrant_insert_error: %s", e)
            return False

    async def _resolve_vector_field(
        self,
        collection: str,
        embedding: list[float],
        content: str,
        client: Any,
    ) -> Any:
        """Build a Qdrant vector payload using the collection's actual names."""
        import rugbrain.qdrant_store as _pkg

        if not await self._collection_uses_named_vectors(collection, client):
            return embedding

        dense_name = await self._get_dense_vector_name(collection, client, len(embedding))
        if dense_name is None:
            logger.error(
                "qdrant_insert_blocked collection=%s reason=missing_dense_vector_name",
                collection,
            )
            return None
        vector_field: dict[str, Any] = {dense_name: embedding}
        sparse_name = await self._get_sparse_vector_name(collection, client)
        if sparse_name is not None:
            sparse = await _pkg._embed_sparse(content)
            if sparse is not None:
                vector_field[sparse_name] = {
                    "indices": sparse[0],
                    "values": sparse[1],
                }
        return vector_field

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
