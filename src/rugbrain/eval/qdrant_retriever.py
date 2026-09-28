"""The DIRECT-QDRANT retriever factory for the offline eval harness.

Bypasses the production search engine. The production engine depends on a
Redis doc-index and a built FAISS index; when either is absent it returns zero
results with no error, which is indistinguishable from a corpus that genuinely
cannot answer. Scoring the collection's stored vectors directly means the eval
exercises the real ANN search against live Qdrant without the full stack — and
without a silent-zero failure mode.

This is a proper ANN search over the full collection. An earlier scroll+cosine
sampled only the first 2,000 points, so gold in a 2M-point collection could
never be found — recall was structurally zero regardless of retrieval quality.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from typing import Any

_NAMED_CACHE: dict[str, bool] = {}


def _named_vectors(base: str, collection: str) -> bool:
    """True when the collection stores named vectors (dense + sparse)."""
    if collection in _NAMED_CACHE:
        return _NAMED_CACHE[collection]
    try:
        with urllib.request.urlopen(f"{base}/collections/{collection}", timeout=30) as resp:  # noqa: S310
            vectors = json.load(resp)["result"]["config"]["params"].get("vectors") or {}
        named = isinstance(vectors, dict) and "size" not in vectors
    except Exception:
        named = False
    _NAMED_CACHE[collection] = named
    return named


def direct_qdrant_retriever(
    qdrant_url: str | None = None,
    embedder: Any | None = None,
) -> Callable[..., Any]:
    """
    Build an offline retriever that queries Qdrant directly (no Redis/FAISS).

    Args:
        qdrant_url: Qdrant base URL (default ``$QDRANT_URL`` or localhost:6334).
        embedder: object with ``async embed_query(str) -> list[float]``; when
            ``None`` the rugbrain embedder is loaded lazily.

    Returns:
        Async ``(query, collection, limit) -> list[dict]`` with ``id`` and
        ``content`` keys, sorted by descending cosine similarity.
    """
    base = (qdrant_url or os.getenv("QDRANT_URL") or "http://127.0.0.1:6334").rstrip("/")

    async def retrieve(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        if not collection:
            return []
        if embedder is not None:
            q_vec = await embedder.embed_query(query)
        else:
            from rugbrain.query_embedder import embed_query

            q_vec = await embed_query(query)

        named = _named_vectors(base, collection)
        vector: Any = {"name": "dense", "vector": q_vec} if named else q_vec
        req = urllib.request.Request(  # noqa: S310 - local Qdrant URL
            f"{base}/collections/{collection}/points/search",
            data=json.dumps({"vector": vector, "limit": limit, "with_payload": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310, ASYNC210  # nosec B310 — local Qdrant URL, eval batch tool
            hits = json.load(resp).get("result", [])
        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.get("payload", {})
            content = " ".join(
                str(payload.get(key) or "")
                for key in ("content", "title", "description", "code_block", "ocr_text")
            )
            results.append(
                {
                    "id": str(hit.get("id")),
                    "content": content,
                    "collection": collection,
                    "score": hit.get("score"),
                }
            )
        return results

    return retrieve


__all__ = ["direct_qdrant_retriever"]
