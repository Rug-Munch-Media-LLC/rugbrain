#!/usr/bin/env python3
"""Shared helpers for the new RugBrain source ingesters.

Used by the Part-1 scam/hack database ingesters (rugdoc, tokensniffer,
honeypot, defisafety, revokecash, chainabuse, cryptoscamdb, peckshield)
so every script embeds with the same Infinity batch path (1024-dim bge-m3),
uses deterministic UUIDv5 point IDs, and writes the canonical Qdrant payload
shape. Extends ``ingest_common.py`` with a batched store write.

Scripts import this module directly:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ingest_common2 import build_doc, write_docs
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore

logger = logging.getLogger(__name__)

from ingest_common import chunk_text, content_hash, embed_many  # noqa: E402


def build_doc(
    *,
    source: str,
    rel_key: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    severity: str = "medium",
    chain: str = "",
    collection: str = "",
) -> dict[str, Any]:
    """Build a canonical ingest doc dict used by all new ingesters.

    The ``rel_key`` (a stable source-specific identifier) is hashed with the
    content so re-running overwrites the same Qdrant points instead of
    duplicating them.
    """
    return {
        "rel_key": f"{source}:{rel_key}",
        "text": content,
        "metadata": metadata or {},
        "severity": severity,
        "chain": chain,
        "collection": collection,
    }


async def write_docs(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    docs: Sequence[dict[str, Any]],
    *,
    source: str,
    collection: str,
    dry_run: bool = False,
    chunk_size: int = 2000,
    chunk_overlap: int = 100,
    progress_every: int = 25,
) -> dict[str, int]:
    """Chunk, embed and upsert *docs* into *collection*.

    Returns ``{"inserted": n, "chunks": n, "failed": n}``. Each doc is
    split into fixed-width windows; every chunk gets its own embedding and
    a deterministic point ID. Embedding failures are skipped, never fatal.
    """
    if dry_run:
        total = 0
        for doc in docs:
            total += max(1, (len(doc["text"]) + chunk_size - 1) // chunk_size)
        logger.info(
            "[DRY RUN] source=%s would ingest %d docs / ~%d chunks into %s",
            source,
            len(docs),
            total,
            collection,
        )
        return {"inserted": 0, "chunks": total, "failed": 0}

    inserted = 0
    chunks = 0
    failed = 0
    for i, doc in enumerate(docs, start=1):
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        windows = chunk_text(text, chunk_size, chunk_overlap)
        vectors = await embed_many(client, [w[:8000] for w in windows])
        for ci, (window, vector) in enumerate(zip(windows, vectors, strict=False)):
            if not vector:
                failed += 1
                continue
            doc_id = f"{doc['rel_key']}:{content_hash(window)}:{ci}"
            metadata = dict(doc.get("metadata") or {})
            metadata["chunk_index"] = ci
            metadata["total_chunks"] = len(windows)
            ok = await store.insert(
                doc_id=doc_id,
                collection=collection,
                embedding=vector,
                content=window,
                metadata=metadata,
                source=source,
                severity=str(doc.get("severity", "medium")),
                chain=str(doc.get("chain", "") or ""),
            )
            if ok:
                inserted += 1
            else:
                failed += 1
            chunks += 1
        if i % progress_every == 0 or i == len(docs):
            logger.info(
                "progress %d/%d docs, %d chunks upserted (%d failed)",
                i,
                len(docs),
                inserted,
                failed,
            )
    logger.info(
        "done source=%s collection=%s docs=%d chunks=%d inserted=%d failed=%d",
        source,
        collection,
        len(docs),
        chunks,
        inserted,
        failed,
    )
    return {"inserted": inserted, "chunks": chunks, "failed": failed}


__all__ = ["build_doc", "write_docs"]
