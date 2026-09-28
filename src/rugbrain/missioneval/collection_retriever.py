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

log = logging.getLogger(__name__)

DEFAULT_QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")

#: The corpus lane the collections live in (see rug_events.embedding).
CORPUS_DIM = 2560


class RetrievalError(RuntimeError):
    """A case's collection could not be searched. Surfaces, never swallowed."""


def _embed_all(texts: list[str]) -> list[list[float]]:
    """One embed lane for the whole eval, resolved once and cached per process."""
    from rugbrain.rug_events.embedding import embed_sync

    return embed_sync(texts)


def _scroll(text: str, collection: str, vector: list[float], top_k: int, url: str) -> list[dict]:
    """Qdrant vector search; returns ``[{id, text}]`` in rank order."""
    body = {
        "query": vector,
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
        out.append(
            {
                "id": str(hit.get("id")),
                "text": payload.get("content") or payload.get("text") or "",
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
        vector = _embed_all([query])[0]
        if len(vector) != CORPUS_DIM:
            raise RetrievalError(f"query vector dim {len(vector)} != corpus {CORPUS_DIM}")
        return await asyncio.to_thread(_scroll, query, target, vector, top_k, url)

    return retrieve


__all__ = ["CORPUS_DIM", "DEFAULT_QDRANT", "RetrievalError", "make_retriever"]
