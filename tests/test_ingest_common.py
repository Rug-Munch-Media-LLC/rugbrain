"""Tests for rugbrain.ingest_common — shared ingestion helpers."""

from __future__ import annotations

import importlib as _il
from typing import Any

ingest_common = _il.import_module("rugbrain.ingest_common")

mod = ingest_common


def test_content_hash_stable_and_short() -> None:
    assert mod.content_hash("hello world") == mod.content_hash("hello world")
    assert len(mod.content_hash("x")) == 16
    assert mod.content_hash("a") != mod.content_hash("b")


def test_chunk_text_short_text() -> None:
    assert mod.chunk_text("") == []
    assert mod.chunk_text("   ") == []
    assert mod.chunk_text("short") == ["short"]


def test_chunk_text_long_text_overlaps() -> None:
    chunks = mod.chunk_text("a" * 5000, size=1000, overlap=100)
    assert len(chunks) >= 5
    assert all(len(c) <= 1000 for c in chunks)
    # Overlap means consecutive chunks share content.
    assert chunks[0][-100:] == chunks[1][:100]


def test_crawl_document_stable_id_deterministic() -> None:
    a = mod.doc_from_article(title="T", content="C", url="https://x/a", source="s", collection="c")
    b = mod.doc_from_article(title="T", content="C", url="https://x/a", source="s", collection="c")
    assert a.stable_id(mod.content_hash("chunk1")) == b.stable_id(mod.content_hash("chunk1"))
    assert a.stable_id("c1") != a.stable_id("c2")


def test_crawl_document_metadata_defaults() -> None:
    doc = mod.doc_from_article(title="T", content="C", url="u", source="s", collection="c")
    assert doc.severity == "medium"
    assert doc.chain == "unknown"
    assert doc.title == "T"
    assert doc.url == "u"
    assert doc.metadata == {}


class _FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed_one(self, text: str) -> list[float]:
        self.calls += 1
        return [0.1] * 1024


class _FakeStore:
    def __init__(self) -> None:
        self.points: list[tuple[str, str]] = []

    async def insert(
        self: Any,
        doc_id: Any,
        collection: Any,
        embedding: Any,
        content: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
        severity: str = "medium",
        chain: str = "",
    ) -> bool:
        self.points.append((doc_id, content))
        return True


async def test_store_documents_embeds_and_inserts() -> None:
    docs = [
        mod.doc_from_article(title="A", content="alpha", url="u1", source="s", collection="c"),
        mod.doc_from_article(title="B", content="beta", url="u2", source="s", collection="c"),
    ]
    embedder = _FakeEmbedder()
    store = _FakeStore()
    result = await mod.store_documents(docs, embedder, store, progress_every=1)
    assert result["inserted"] == 2
    assert result["failed"] == 0
    assert result["chunks"] == 2
    assert embedder.calls == 2
    assert len(store.points) == 2


async def test_store_documents_failed_embed_continues() -> None:
    class _BrokenEmbedder:
        async def embed_one(self, text: str) -> list[float]:
            raise RuntimeError("embedding backend down")

    docs = [mod.doc_from_article(title="A", content="alpha", url="u1", source="s", collection="c")]
    result = await mod.store_documents(docs, _BrokenEmbedder(), _FakeStore())
    assert result["inserted"] == 0
    assert result["failed"] == 1
