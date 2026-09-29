"""Per-collection retrieval for the mission eval.

The eval asks a question and names the collection that should answer it
(``MissionCase.collection``). The production retriever cannot serve that shape:
``DualTierRetriever._tier_retrieve`` searches a single module-level
``COLLECTION`` (``RAG_COLLECTION``, default ``crypto_intelligence`` — a
collection that does not exist in this deployment) and it embeds through the
local llama.cpp lane on :8004, which is currently down. Pointed at it, every
case returns zero documents and *no error* — the runner records a clean miss,
so a dead infrastructure path and an empty corpus look identical. That is
precisely why ``rug_history`` has been reading 0.000.

This adapter searches the collection the case actually names, using the
canonical batched embedder, and returns the same ``{id, text}`` shape the
grader expects. It also does not swallow failures: a collection that cannot be
searched raises, so the runner records a per-case error instead of a silent
miss.

Deliberately a build/eval dependency-free path: Qdrant REST + the repo's own
embedder, no retriever class, no rerank tier.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

import httpx

from rugbrain.dim_policy import COLLECTION_DIM as CANONICAL_DIM
from rugbrain.qdrant_store.lane_dims import fetch_live_dim
from rugbrain.qdrant_store.lane_resolver import resolve_collection

log = logging.getLogger(__name__)

DEFAULT_QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")

#: The CANONICAL corpus lane. Previously a hardcoded 2560 with no resolver
#: call, so every case had to name a 2560 BASE collection by hand. Two real
#: consequences:
#:   1. Naming an _8b twin returned "400 expected dim: 4096, got 2560" and was
#:      recorded as an ERROR rather than a miss — the eval could not tell "the
#:      corpus does not cover this" from "the harness is on the wrong lane".
#:   2. A negative case could not target wallet_clusters (1024-dim, pinned) or
#:      exchange_flows, because pinning made the run error instead of missing.
#: It now reads the registry like the write path and the search path do.
#: Kept for callers that import it. It is the canonical lane, not "the" lane.
CORPUS_DIM = CANONICAL_DIM


class RetrievalError(RuntimeError):
    """A case's collection could not be searched. Surfaces, never swallowed."""


async def _live_width(target: str) -> int | None:
    """Live width of the resolved collection, or None when it does not exist."""
    url = os.getenv("QDRANT_URL", DEFAULT_QDRANT)
    async with httpx.AsyncClient(base_url=url) as probe:
        return await fetch_live_dim(resolve_collection(target), probe)


async def _embed_for(target: str, text: str) -> list[float]:
    """Embed on the lane the TARGET collection actually lives in.

    rug_events.embedding pins 2560/4B. That pin is CORRECT for rug_events,
    which is genuinely 2560 with no _8b twin, and WRONG for everything else
    now that the 8B migration is complete. Using it unconditionally produced a
    2560 vector against a 4096 store: 68 errors, gold_rate 0.0.

    A width mismatch RAISES rather than truncates. An 8B vector squeezed into a
    4B index stores and searches fine and means nothing.
    """
    live = await _live_width(target)
    if live == CANONICAL_DIM:
        from rugbrain.embeddings import get_embedding

        return await get_embedding(text, query=True)
    if live == 2560:
        from rugbrain.rug_events.embedding import apply_lane_env, embed_batch

        apply_lane_env()
        return (await embed_batch([text]))[0]
    raise RetrievalError(
        f"cannot embed for {target}: live width {live} matches no known lane"
    )



def _scroll(text: str, collection: str, vector: list[float], top_k: int, url: str) -> list[dict]:
    """Qdrant vector search; returns ``[{id, text}]`` in rank order.

    The body key is ``vector``, not ``query``: this Qdrant build's REST search
    endpoint takes ``vector`` and answers the ``query`` spelling with
    ``missing field 'vector'``, which is unhelpful enough to look like a
    server fault rather than a schema difference.
    """
    body = {
        "vector": vector,
        "limit": top_k,
        "with_payload": True,
        "with_vector": False,
    }
    req = urllib.request.Request(  # noqa: S310 — operator-supplied Qdrant host
        f"{url.rstrip('/')}/collections/{collection}/points/search",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            hits = json.load(resp).get("result") or []
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode(errors="replace")
        raise RetrievalError(f"qdrant search {collection} -> {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RetrievalError(f"qdrant unreachable at {url}: {exc.reason}") from exc
    out: list[dict[str, Any]] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        body = payload.get("content") or payload.get("text") or ""
        out.append(
            {
                "id": str(hit.get("id")),
                # The grader reads `content` (see missioneval.judge:79). Emitting
                # only `text` makes every term match fail and every case score a
                # clean `miss` — a silent zero, not an error. Both keys are set
                # so the doc satisfies the judge and stays readable.
                "content": body,
                "text": body,
                "meta": payload,
            }
        )
    return out


def make_retriever(url: str = DEFAULT_QDRANT):
    """Build the ``(query, collection, top_k) -> docs`` callable the runner wants.

    Returned coroutine function, matching ``missioneval.runner``'s retriever
    contract. One shared event loop per call is unnecessary: the embedder is
    sync-wrapped and the HTTP call is blocking, so this runs inside the
    runner's own loop without nested ``asyncio.run``.
    """

    async def retrieve(query: str, collection: str | None, top_k: int) -> list[dict[str, Any]]:
        target = collection or os.getenv("RAG_COLLECTION", "crypto_intelligence")
        # Resolve ONCE, here, and use the resolved name for the search as well
        # as for the embed. Previously the resolution happened only inside the
        # embedding helper, so _scroll was still handed the base name and Qdrant
        # answered "Wrong input: Vector dimension error" — a 400 that looked like
        # a lane problem and was actually a name problem.
        target = resolve_collection(target)
        vector = await _embed_for(target, query)
        # Assert against the lane we just embedded FOR, not a single global dim.
        # A global dim is simultaneously too strict for the collection that
        # genuinely needs 2560 and blind to a real mismatch everywhere else.
        live = await _live_width(target)
        if live is not None and len(vector) != live:
            raise RetrievalError(
                f"query vector dim {len(vector)} != live width {live} for {target}"
            )
        return await asyncio.to_thread(_scroll, query, target, vector, top_k, url)

    return retrieve


__all__ = ["CORPUS_DIM", "DEFAULT_QDRANT", "RetrievalError", "make_retriever"]
