"""Shared ingestion helpers for RugBrain crawl/source pipelines.

Provides the small, dependency-free pieces every ingestion path needs so the
per-source modules (``web_crawler``, ``sources_registry``, ``social_intel``,
``market_data``) stay focused on fetching:

  * ``CrawlDocument``     — canonical document shape before embedding.
  * ``chunk_text``        — overlap-window chunking for long articles.
  * ``content_hash``      — stable content fingerprint for point IDs.
  * ``store_documents``   — embed + upsert into Qdrant with idempotent IDs.

Deterministic point IDs (UUIDv5 seeded from ``source + url + chunk hash``)
make every upsert idempotent: re-running a pipeline overwrites the same
points instead of duplicating them.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Protocol

from pydantic import BaseModel, Field

# Crypto-atom helpers: address/hash/URL preservation inside chunk windows.
from rugbrain.app_chunking import _protect_atoms, _restore_atoms, _snap_pos

log = logging.getLogger(__name__)


class CrawlDocument(BaseModel):
    """A fetched article/post/feed item ready for chunking + embedding.

    ``id`` is deterministic (UUIDv5 string) so Qdrant upserts overwrite the
    same point on re-ingest. ``content`` holds the clean extracted text.
    """

    title: str = ""
    content: str
    url: str = ""
    source: str = ""
    collection: str = "default"
    category: str = ""
    published: str = ""
    severity: str = "medium"
    chain: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def stable_id(self, chunk_hash: str) -> str:
        seed = f"{self.source}:{self.url}:{chunk_hash}".strip(":")
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))


class Embedder(Protocol):
    """Minimal embedder contract (matches Qwen3Embedder)."""

    async def embed_one(self, text: str) -> list[float]: ...


class VectorStore(Protocol):
    """Minimal store contract (matches QdrantVectorStore.insert)."""

    async def insert(
        self,
        doc_id: str,
        collection: str,
        embedding: list[float],
        content: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
        severity: str = "medium",
        chain: str = "",
    ) -> bool: ...

    async def has_point(self, doc_id: str, collection: str) -> bool:
        """True when the deterministic point for *doc_id* is already stored.

        Optional: callers use ``getattr(store, "has_point", None)`` and treat a
        missing implementation as unknown (proceed to embed) so the existence
        gate never blocks fresh ingestion.
        """
        ...


def content_hash(text: str) -> str:
    """Short stable SHA-256 fingerprint of *text* (first 16 hex chars)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunk_text(text: str, size: int = 1500, overlap: int = 100) -> list[str]:
    """Split *text* into overlapping windows of at most *size* chars.

    Crypto-aware: EVM/Solana addresses, hashes and URLs are never split in
    half — a window boundary that would cut one is pushed just past it.
    Mirrors the chunking used by ``scripts/ingest_rekt_news.py`` so all
    RugBrain pipelines produce consistently-sized embedded chunks.
    """
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    protected, atoms = _protect_atoms(text)
    chunks: list[str] = []
    start = 0
    while start < len(protected):
        end = min(start + size, len(protected))
        end = _snap_pos(protected, end)
        chunk = protected[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(protected):
            break
        start = max(end - overlap, start + 1)
        start = _snap_pos(protected, start)
    return [_restore_atoms(c, atoms) for c in chunks]


async def store_documents(
    docs: list[CrawlDocument],
    embedder: Embedder,
    store: VectorStore,
    chunk_size: int = 1500,
    chunk_overlap: int = 100,
    progress_every: int = 25,
) -> dict[str, int]:
    """Embed and upsert *docs* into Qdrant.

    Returns ``{"inserted": n, "failed": n, "chunks": n, "skipped": n}``.
    Each document is chunked, every chunk is embedded and upserted with a
    deterministic ID. ``skipped`` counts chunks already present in the store
    (checked via ``store.has_point`` when available) — those are NOT re-embedded.
    A failed embedding/storage increments ``failed`` and continues so one
    bad item never aborts the batch.
    """
    inserted = 0
    failed = 0
    chunks = 0
    skipped = 0
    total = len(docs)
    # Optional existence gate: pipelines that re-run on a cadence (news_crawl,
    # full_recrawl, source ingestion) would otherwise re-embed every chunk on
    # every run. When the store exposes ``has_point`` and the deterministic
    # point id already exists, the chunk is skipped WITHOUT calling the
    # embedder — this is what keeps the embedder free for interactive traffic.
    # Fail-open: a missing implementation or a check error falls through to
    # embed (never drops fresh content).
    has_point = getattr(store, "has_point", None)
    for i, doc in enumerate(docs, start=1):
        for chunk in chunk_text(doc.content, chunk_size, chunk_overlap):
            doc_id = doc.stable_id(content_hash(chunk))
            metadata = dict(doc.metadata)
            metadata.setdefault("title", doc.title)
            metadata.setdefault("url", doc.url)
            metadata.setdefault("category", doc.category)
            metadata.setdefault("published", doc.published)
            metadata.setdefault("source", doc.source)
            try:
                if has_point is not None and await has_point(doc_id, doc.collection):
                    skipped += 1
                    continue
                embedding = await embedder.embed_one(chunk[:8000])
            except Exception as e:
                log.warning("store_documents_embed_failed src=%s err=%s", doc.source, str(e)[:120])
                failed += 1
                continue
            if not embedding:
                failed += 1
                continue
            try:
                ok = await store.insert(
                    doc_id=doc_id,
                    collection=doc.collection,
                    embedding=embedding,
                    content=chunk,
                    metadata=metadata,
                    source=doc.source,
                    severity=doc.severity,
                    chain=doc.chain,
                )
            except Exception as e:
                log.warning("store_documents_upsert_failed src=%s err=%s", doc.source, str(e)[:120])
                ok = False
            if ok:
                inserted += 1
            else:
                failed += 1
            chunks += 1
        if i % progress_every == 0 or i == total:
            log.info(
                "store_documents_progress %d/%d inserted=%d failed=%d chunks=%d",
                i,
                total,
                inserted,
                failed,
                chunks,
            )
    log.info(
        "store_documents_done total=%d inserted=%d failed=%d chunks=%d",
        total,
        inserted,
        failed,
        chunks,
    )
    return {"inserted": inserted, "failed": failed, "chunks": chunks, "skipped": skipped}


def doc_from_article(
    *,
    title: str,
    content: str,
    url: str,
    source: str,
    collection: str,
    category: str = "",
    published: str = "",
    severity: str = "medium",
    chain: str = "unknown",
    metadata: dict[str, Any] | None = None,
) -> CrawlDocument:
    """Convenience constructor for the common article-shaped payload."""
    return CrawlDocument(
        title=(title or "").strip()[:300],
        content=(content or "").strip(),
        url=(url or "").strip(),
        source=source,
        collection=collection,
        category=category,
        published=published,
        severity=severity,
        chain=chain,
        metadata=dict(metadata or {}),
    )


__all__ = [
    "CrawlDocument",
    "Embedder",
    "VectorStore",
    "chunk_text",
    "content_hash",
    "doc_from_article",
    "store_documents",
]
