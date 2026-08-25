"""Tests for the unified news feed API (rugbrain.news_feed).

Contract rewritten 2026-08-25: the module was replaced by the unified
feed implementation (commit 3e62fa2) serving ``/api/v1/feed/all`` +
``/api/v1/feed/sources``; the old story/trending/topics endpoints and
their tests were removed with it. These tests pin the CURRENT contract:

* /feed/all merges hybrid_search results across collections, tags each
  hit with its source key, sorts by feed date desc, applies category /
  min_date filters, respects limit, and FAILS OPEN (200 + empty) when
  the embedder is unavailable.
* /feed/sources aggregates the ingester source registries.

Patching note: news_feed imports ``get_embedding`` and
``QdrantVectorStore`` INSIDE the endpoint function, so patches must
target their source modules (rugbrain.embeddings / rugbrain.qdrant_store),
not the news_feed namespace.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def client() -> Any:
    from fastapi import FastAPI

    from rugbrain.news_feed import news_feed_router

    app = FastAPI()
    app.include_router(news_feed_router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _hit(meta: dict[str, Any], score: float = 0.9) -> dict[str, Any]:
    return {"score": score, "payload": dict(meta), "metadata": dict(meta)}


class _FakeStore:
    """Hybrid-search double: returns canned rows per collection."""

    def __init__(self, per_collection: dict[str, list[dict[str, Any]]]) -> None:
        self._data = per_collection
        self.calls: list[tuple[str, int]] = []

    async def hybrid_search(
        self,
        query_text: str = "",
        query_embedding: Any = None,
        collection: str = "",
        limit: int = 10,
        min_similarity: float = 0.0,
        **_: Any,
    ) -> list[dict[str, Any]]:
        self.calls.append((collection, limit))
        return list(self._data.get(collection, []))


@pytest.fixture
def mock_rag(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    """Patch embeddings + QdrantVectorStore at their source modules."""
    store = _FakeStore(
        {
            "youtube_videos": [
                _hit({"title": "ETH deep dive", "upload_date": "2026-08-20T10:00:00+00:00"}),
            ],
            "podcast_episodes": [
                _hit({"title": "Pod: ETFs", "ingested_at": "2026-08-21T09:00:00+00:00"}),
            ],
            "threat_intel": [
                _hit({"title": "SEC approves ETF", "date": "2026-08-22T08:00:00+00:00"}),
                # missing every date field -> falls back to now (renders last)
                _hit({"title": "Undated flash"}),
            ],
        }
    )

    async def fake_embedding(_q: str) -> list[float]:
        return [0.1] * 8

    import rugbrain.embeddings as emb_mod
    import rugbrain.qdrant_store as qdrant_mod

    monkeypatch.setattr(emb_mod, "get_embedding", fake_embedding, raising=False)
    monkeypatch.setattr(qdrant_mod, "QdrantVectorStore", lambda: store, raising=False)
    return store


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/feed/all
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_feed_all_merges_sources_sorted_by_date(client: Any, mock_rag: _FakeStore) -> None:
    resp = await client.get("/api/v1/feed/all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    titles = [r["metadata"]["title"] for r in data["results"]]
    # Date-descending across merged sources. Undated hits fall back to
    # datetime.now() -> treated as freshest, render first.
    assert titles.index("SEC approves ETF") < titles.index("Pod: ETFs")
    assert titles.index("Pod: ETFs") < titles.index("ETH deep dive")
    assert titles[0] == "Undated flash"
    # Every hit is tagged with its source key.
    src_tags = {r["metadata"]["_source"] for r in data["results"]}
    assert src_tags == {"youtube", "podcast", "news"}
    assert set(data["sources_queried"]) == {"youtube", "podcast", "newsletter", "news"}


@pytest.mark.asyncio
async def test_feed_all_respects_limit(client: Any, mock_rag: _FakeStore) -> None:
    resp = await client.get("/api/v1/feed/all", params={"limit": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_feed_all_min_date_and_category_filters(client: Any, mock_rag: _FakeStore) -> None:
    resp = await client.get("/api/v1/feed/all", params={"min_date": "2026-08-21"})
    data = resp.json()
    titles = [r["metadata"]["title"] for r in data["results"]]
    # Dated hits older than min_date are dropped; undated (fallback=now)
    # passes the filter.
    assert "ETH deep dive" not in titles
    assert "Undated flash" in titles
    dates = [r["metadata"]["_feed_date"][:10] for r in data["results"]]
    assert all(d >= "2026-08-21" for d in dates)

    # Category filter drops everything when no metadata matches.
    resp = await client.get("/api/v1/feed/all", params={"category": "nope"})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_feed_all_fails_open_without_embedder(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import rugbrain.embeddings as emb_mod

    async def boom(_q: str) -> list[float]:
        raise RuntimeError("embedder down")

    monkeypatch.setattr(emb_mod, "get_embedding", boom, raising=False)
    resp = await client.get("/api/v1/feed/all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0
    assert "embedder down" in data["error"]


@pytest.mark.asyncio
async def test_feed_all_unknown_source_skipped(client: Any, mock_rag: _FakeStore) -> None:
    resp = await client.get("/api/v1/feed/all", params={"sources": "youtube,bogus"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["sources_queried"] == ["youtube", "bogus"]
    # Only the youtube collection was queried.
    assert {c for c, _ in mock_rag.calls} == {"youtube_videos"}


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/feed/sources
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_feed_sources_aggregates_registries(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "rugbrain.youtube_ingester.YT_CHANNELS",
        [("a", "u", "news"), ("b", "u", "markets")],
        raising=False,
    )
    monkeypatch.setattr(
        "rugbrain.podcast_ingester.CRYPTO_PODCASTS", [("p", "u", "n", "news")], raising=False
    )
    monkeypatch.setattr(
        "rugbrain.newsletter_ingester.NEWSLETTERS", [("n1", "u", "d", "news")], raising=False
    )

    resp = await client.get("/api/v1/feed/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert data["youtube"]["total"] == 2
    assert data["podcasts"]["total"] == 1
    assert data["newsletters"]["total"] == 1
    assert data["total_sources"] == 4
