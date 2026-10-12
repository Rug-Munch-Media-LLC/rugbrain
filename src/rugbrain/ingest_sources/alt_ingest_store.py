"""Embed + upsert stage for the Telegram channel alt-ingest (8B lane only).

Split out of ``alt_ingest`` so each module keeps one job: the other module
fetches and dedupes, this one embeds and writes. It is also the unit under
test: no Telethon, no network — a fake embedder and a fake store are enough.

8B contract (non-negotiable):
    * embedding via :func:`rugbrain.embedder.get_embedder` (hosted
      Qwen/Qwen3-Embedding-8B chain) — never a custom embed HTTP call;
    * 4096-dim enforced before the write, a wrong dim is a FAILURE not a
      silent drop;
    * target ``news_v2`` → the store resolves ``news_v2_8b``;
    * gate 1 (``has_point`` on the deterministic point id) runs BEFORE the
      embed, so a re-run costs zero tokens for content already stored.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from rugbrain.embedder import get_embedder
from rugbrain.ingest_sources.alt_ingest_payload import COLLECTION, content_hash, point_id_for_doc
from rugbrain.qdrant_store import QdrantVectorStore

logger = logging.getLogger("rugbrain.tg_alt_ingest_store")

__all__ = ["CANONICAL_DIM", "EMBED_BATCH", "MAX_CONTENT_CHARS", "embed_and_store"]

#: Canonical 8B lane width. Anything else is a failure, never a truncation.
CANONICAL_DIM = 4096

#: Bounded embed/store batch: keeps the DeepInfra request inside the hosted
#: lane's per-request budget and makes the sweep resumable at batch edges.
EMBED_BATCH = 8

#: The store caps the payload itself; cap our pre-store copy so a huge
#: forwarded album message cannot blow a batch.
MAX_CONTENT_CHARS = 10000


def _point_id_for(doc: Any) -> str:
    chan_id = int(doc.metadata.get("channel_id", 0))
    msg_id = int(doc.metadata.get("msg_id", 0))
    return point_id_for_doc(chan_id, msg_id, content_hash(doc.content))


async def embed_and_store(
    docs: list[Any],
    summary: dict[str, Any],
    *,
    embedder_factory: Callable[[], Any] | None = None,
    store_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Embed *docs* on the 8B lane and upsert them in bounded batches.

    Increments ``summary`` counters in place
    (``duplicate_msgid``/``inserted``/``failed``) so the CLI report can state
    exactly how many candidates were skipped as already-stored.
    """
    embedder = (embedder_factory or get_embedder)()
    store = store_factory() if store_factory is not None else QdrantVectorStore()
    has_point = getattr(store, "has_point", None)
    try:
        for start in range(0, len(docs), EMBED_BATCH):
            for doc in docs[start : start + EMBED_BATCH]:
                chan_id = int(doc.metadata.get("channel_id", 0))
                # Gate 1: (channel_id, message_id) — never re-store a message.
                if has_point is not None and await has_point(_point_id_for(doc), COLLECTION):
                    summary["duplicate_msgid"] += 1
                    continue
                try:
                    embedding = await embedder.embed_one(doc.content[:MAX_CONTENT_CHARS])
                except Exception as exc:
                    summary["failed"] += 1
                    logger.warning("alt_ingest_embed_failed ch=%s err=%s", chan_id, str(exc)[:120])
                    continue
                if len(embedding) != CANONICAL_DIM:
                    logger.warning("alt_ingest_bad_dim ch=%s dim=%d", chan_id, len(embedding))
                    summary["failed"] += 1
                    continue
                ok = await store.insert(
                    doc_id=_point_id_for(doc),
                    collection=COLLECTION,
                    embedding=embedding,
                    content=doc.content[:MAX_CONTENT_CHARS],
                    metadata=doc.metadata,
                    source=doc.source,
                    severity=doc.severity,
                    chain=doc.chain,
                )
                if ok:
                    summary["inserted"] += 1
                else:
                    summary["failed"] += 1
    finally:
        await store.close()
    return summary
