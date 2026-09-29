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

LANE RESOLUTION IS NOT OPTIONAL HERE
    This is the third instance of one defect (the first two: ``be0da01`` in the
    search path, ``24915db`` in the mission-eval retriever): a component that
    names a collection to Qdrant without going through the same resolver the
    write path uses. This retriever sent the golden row's collection verbatim
    into ``/collections/{name}/points/search``.

    WHY THAT WAS A REAL ERROR, NOT COSMETIC
    The ``HTTPError`` from a width mismatch was UNGUARDED, so it propagated to
    ``query_scoring.score_query``, which records ``QueryScore.error``. A
    per-case ERROR is indistinguishable from a corpus that genuinely cannot
    answer — precisely the confusion a retrieval eval exists to avoid.

    THE PART THAT IS SUBTLE, AND WHY A NAIVE FIX IS WRONG
    Both generations are real and populated, so "resolve the name" alone is not
    the fix — it is only half of it. Measured live on :6334 (2026-09-29,
    search-only):

        defi_exploits      2560   @2560 -> 200   @4096 -> 400
        defi_exploits_8b   4096   @2560 -> 400   @4096 -> 200

    A single fixed embedding lane therefore cannot be correct for both halves.
    This retriever embedded through ``query_embedder.embed_query`` (Qwen3-
    Embedding-4B, :8004) and measured **2560**, NOT the 4096 that
    ``dim_policy.COLLECTION_DIM`` declares. Resolving the name while keeping
    that embedder turns every working row into HTTP 400 — the resolver applied
    alone is a REGRESSION here, and was caught by running the retriever live
    rather than by reading the table.

    So the rule is the one ``24915db`` established: resolve the name ONCE, read
    the LIVE width of what it resolved to, and embed on that lane.
      * 4096 -> ``embeddings.get_embedding`` (Qwen3-Embedding-8B, canonical)
      * 2560 -> ``query_embedder.embed_query`` (Qwen3-Embedding-4B, :8004)

    The 2560 leg is DELIBERATE, not a leftover pin: ``coding_knowledge`` and
    ``solana_patterns`` are pinned to the retired generation with no ``_8b``
    twin (see ``lane_table.PINNED_DIMS``), so a 2560 vector is the only vector
    those collections can ever accept. ``dim_policy.COLLECTION_DIM`` being 4096
    while this file's 2560 leg exists is not a contradiction — it is the
    migration being incomplete on the pinned collections, and it is exactly the
    hazard ``lane_widths.py`` documents.

    A width matching no known lane RAISES. It is never guessed and never
    truncated: an 8B vector squeezed into a 4B index searches fine and means
    nothing.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from rugbrain.qdrant_store.lane_resolver import resolve_collection

_NAMED_CACHE: dict[str, bool] = {}
_WIDTH_CACHE: dict[str, int | None] = {}


class LaneMismatch(RuntimeError):
    """The resolved collection's live width matches no embedding lane we hold."""


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


def _live_width(base: str, collection: str) -> int | None:
    """Live vector width of *collection*, or None when it cannot be read.

    Read live rather than declared because the declared table and the physical
    collection have already drifted from each other once on this exact axis —
    ``lane_widths.py`` exists solely to record that lesson. A GET on
    ``/collections/{name}`` is cheap and cannot lie about what is stored.
    """
    if collection in _WIDTH_CACHE:
        return _WIDTH_CACHE[collection]
    try:
        with urllib.request.urlopen(f"{base}/collections/{collection}", timeout=30) as resp:  # noqa: S310
            vectors = json.load(resp)["result"]["config"]["params"].get("vectors") or {}
        if "size" in vectors:
            width: int | None = int(vectors["size"])
        else:  # named vectors — the dense one is the lane that matters
            named = vectors.get("dense") or (next(iter(vectors.values())) if vectors else None)
            width = int(named["size"]) if isinstance(named, dict) else None
    except Exception:
        width = None
    _WIDTH_CACHE[collection] = width
    return width


async def _embed_for_width(width: int, query: str) -> list[float]:
    """Embed *query* on the lane whose width is *width*.

    The two lanes are both real and both in service; the canonical one is
    4096 (Qwen3-Embedding-8B) and the preserved one is 2560 (Qwen3-Embedding-
    4B, local :8004) for the collections that never migrated. Kept in one place
    so the mapping "width -> embedder" is stated once and cannot drift the way
    the name->collection mapping did.
    """
    if width == 4096:
        from rugbrain.embeddings import get_embedding

        return await get_embedding(query, query=True)
    if width == 2560:
        from rugbrain.query_embedder import embed_query

        return await embed_query(query)
    raise LaneMismatch(f"no embedding lane for width {width}; refusing to guess")


def direct_qdrant_retriever(
    qdrant_url: str | None = None,
    embedder: Any | None = None,
) -> Callable[..., Any]:
    """
    Build an offline retriever that queries Qdrant directly (no Redis/FAISS).

    Args:
        qdrant_url: Qdrant base URL (default ``$QDRANT_URL`` or localhost:6334).
        embedder: object with ``async embed_query(str) -> list[float]``; when
            ``None`` the lane is chosen from the resolved collection's live
            width. An explicit embedder overrides lane selection — the caller
            then owns the width and must match it to the collection.

    Returns:
        Async ``(query, collection, limit) -> list[dict]`` with ``id`` and
        ``content`` keys, sorted by descending cosine similarity.
    """
    base = (qdrant_url or os.getenv("QDRANT_URL") or "http://127.0.0.1:6334").rstrip("/")

    async def retrieve(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        if not collection:
            return []
        # THE CHOKE POINT, then the lane. Resolving alone is not enough: both
        # generations are populated, so the embedder must match whatever the
        # name resolved TO. See the module docstring for the measured widths.
        collection = resolve_collection(collection) or collection
        if embedder is not None:
            q_vec = await embedder.embed_query(query)
        else:
            width = _live_width(base, collection)
            if width is None:
                raise LaneMismatch(f"cannot read live width of {collection!r}")
            q_vec = await _embed_for_width(width, query)

        named = _named_vectors(base, collection)
        vector: Any = {"name": "dense", "vector": q_vec} if named else q_vec
        req = urllib.request.Request(  # noqa: S310 - local Qdrant URL
            f"{base}/collections/{collection}/points/search",
            data=json.dumps({"vector": vector, "limit": limit, "with_payload": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        # A width mismatch here means the lane selection above disagreed with
        # the collection it was asked about. Say THAT, rather than surfacing a
        # bare HTTPError whose text ("expected dim: 4096, got 2560") does not
        # name the collection and reads like a Qdrant fault.
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310, ASYNC210  # nosec B310 — local Qdrant URL, eval batch tool
                hits = json.load(resp).get("result", [])
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode(errors="replace")
            raise LaneMismatch(
                f"qdrant search {collection} -> {exc.code}: {detail} "
                f"(embedded width {len(q_vec)}, named_vectors={named})"
            ) from exc
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


__all__ = ["LaneMismatch", "direct_qdrant_retriever"]
