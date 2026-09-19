"""QdrantVectorStore.scroll — read-only point paging (no vector query).

Restored verbatim from the pre-split ``qdrant_store.py``: the package split
dropped it and the type-checker caught consumers calling ``scroll`` on the
store (scanner_feedback, trending, content_engine, news_realtime).
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.qdrant_store import _types
from rugbrain.qdrant_store.collections import _map_collection
from rugbrain.qdrant_store.filters import apply_payload_filter, normalize_filters

logger = logging.getLogger(__name__)


class ScrollMixin(_types.StoreSurface):
    """Read-only scroll API used by ingestion-side consumers."""

    async def scroll(
        self,
        collection: str | None,
        limit: int = 100,
        offset: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Scroll through points in *collection* returning ``(results, next_offset)``.

        Used by ingestion-side consumers (scam pattern extraction) to read
        back every point in a collection without a vector query. Results are
        formatted like ``search`` (id + payload fields) with ``similarity``
        omitted (scroll has no score). Returns the next page offset when more
        points remain, else ``None``.
        """
        collection = _map_collection(collection)
        base_collection = collection or "default"
        coll = collection or "default"
        client = await self._get_client()
        # NOTE: no _ensure_collection here — scroll is a READ and must never
        # create collections. The old size-1 placeholder creation poisoned
        # namespaces (a later real insert then hit "expected dim: 1").
        # A missing collection returns 404 below → empty page, which is the
        # correct answer for "nothing ingested yet". Exception: when the
        # interim suffix routed us to a shadow twin that was never created
        # (legacy collections with no _i mirror), read through to the
        # canonical namespace once instead of warning-spamming every cycle.

        payload: dict[str, Any] = {
            "limit": limit,
            "with_payload": True,
        }
        if offset:
            payload["offset"] = offset
        if filters:
            qdrant_filter = normalize_filters(filters)
            apply_payload_filter(payload, qdrant_filter)

        try:
            r = await client.post(f"/collections/{coll}/points/scroll", json=payload)
            if r.status_code == 404 and coll != base_collection:
                # Shadow twin doesn't exist for this legacy collection —
                # read through to the canonical namespace (still read-only).
                logger.debug("qdrant_scroll_shadow_missing read_through base=%s", base_collection)
                coll = base_collection
                r = await client.post(f"/collections/{coll}/points/scroll", json=payload)
            if r.status_code != 200:
                logger.warning("qdrant_scroll_failed status=%d body=%s", r.status_code, r.text)
                return [], None
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", {}).get("points", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            next_offset = data.get("result", {}).get("next_page_offset")
            return results, next_offset
        except Exception as e:
            logger.warning("qdrant_scroll_error: %s", e)
            return [], None
