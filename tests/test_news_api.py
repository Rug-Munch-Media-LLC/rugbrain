"""Tests for the news rating + fear index API endpoints."""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def client() -> Any:
    """Create a test client for the RAG routers."""
    from fastapi import FastAPI

    from rugbrain.endpoints import router, scam_check_router

    app = FastAPI()
    app.include_router(router)
    app.include_router(scam_check_router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/news/rate
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_news_rate_returns_full_breakdown(client: Any) -> None:
    resp = await client.get(
        "/api/v1/news/rate",
        params={
            "url": "https://coindesk.com/article/1",
            "source": "CoinDesk",
            "title": "Exploit post-mortem",
            "content": (
                "Uniswap lost $12.4 million when an attacker drained the "
                "liquidity pool via a reentrancy exploit. The attacker address "
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa moved 2,500 ETH. "
                "Aave and Compound positions were liquidated as collateral "
                "dropped 34%."
            ),
            "published_at": "2026-08-04T09:00:00Z",
            "corroborating_sources": 4,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://coindesk.com/article/1"
    assert set(data["axes"].keys()) == {
        "source_credibility",
        "factual_density",
        "timeliness",
        "bias",
        "corroboration",
        "technical_depth",
        "source_diversity",
    }
    assert 0 <= data["composite"] <= 100
    assert data["tier"] in ("excellent", "good", "fair", "poor")


@pytest.mark.asyncio
async def test_news_rate_coindesk_high_credibility(client: Any) -> None:
    resp = await client.get(
        "/api/v1/news/rate",
        params={"url": "https://coindesk.com/article/2"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["axes"]["source_credibility"] >= 90
    assert data["source_reputation"] == "established"


@pytest.mark.asyncio
async def test_news_rate_missing_url_rejected(client: Any) -> None:
    resp = await client.get("/api/v1/news/rate")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_news_rate_never_fetches_url(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The endpoint must not make an outbound request for the URL (SSRF-safe)."""
    import httpx

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("endpoint attempted a server-side fetch")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    resp = await client.get(
        "/api/v1/news/rate",
        params={"url": "https://coindesk.com/x", "content": "some body text"},
    )
    assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/news/top
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_news_top_fails_open_without_qdrant(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Qdrant unreachable → 200 with empty articles (fail-open).

    Hermetic since 2026-09-14: the suite used to rely on Qdrant genuinely
    being unreachable on the host; with the outage repaired, the dead store
    is simulated explicitly.
    """
    import rugbrain.qdrant_store as qs

    class _DeadStore:
        async def scroll(self, collection: Any, limit: int = 100, offset: Any = None) -> Any:
            raise ConnectionError("qdrant unreachable (simulated)")

    monkeypatch.setattr(qs, "QdrantVectorStore", _DeadStore)
    resp = await client.get("/api/v1/news/top", params={"min_quality": 70})
    assert resp.status_code == 200
    data = resp.json()
    assert data["articles"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_news_top_sorts_by_quality(client: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """With a fake store, returns rated articles sorted by quality desc."""

    class FakeStore:
        async def scroll(
            self,
            collection: Any,
            limit: int = 100,
            offset: Any = None,
        ) -> Any:
            if offset == "done":
                return [], None
            return (
                [
                    {
                        "id": "1",
                        "source": "coindesk.com",
                        "metadata": {
                            "title": "Good article",
                            "source": "CoinDesk",
                            "url": "https://coindesk.com/1",
                            "category": "security",
                            "rating": {
                                "factuality": "verified",
                                "source_reputation": "established",
                                "corroborating_sources": 4,
                                "on_chain_evidence": True,
                                "axes": {"source_credibility": 95},
                                "composite": 88,
                                "quality_score": 88,
                                "tier": "excellent",
                            },
                        },
                    },
                    {
                        "id": "2",
                        "source": "random-blog.example",
                        "metadata": {
                            "title": "Meh article",
                            "source": "RandomBlog",
                            "url": "https://random-blog.example/1",
                            "category": "security",
                            "rating": {
                                "factuality": "rumor",
                                "source_reputation": "unknown",
                                "corroborating_sources": 0,
                                "on_chain_evidence": False,
                                "axes": {"source_credibility": 45},
                                "composite": 55,
                                "quality_score": 55,
                                "tier": "fair",
                            },
                        },
                    },
                ],
                "done",
            )

    import rugbrain.qdrant_store as qs

    monkeypatch.setattr(qs, "QdrantVectorStore", FakeStore)
    resp = await client.get(
        "/api/v1/news/top", params={"category": "security", "min_quality": 70, "limit": 10}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [a["id"] for a in data["articles"]] == ["1"]  # only quality >= 70
    assert data["articles"][0]["quality"] == 88
    assert data["articles"][0]["tier"] == "excellent"


@pytest.mark.asyncio
async def test_news_top_category_filter(client: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Category filter excludes mismatched articles."""

    class FakeStore:
        async def scroll(
            self,
            collection: Any,
            limit: int = 100,
            offset: Any = None,
        ) -> Any:
            return (
                [
                    {
                        "id": "a",
                        "source": "coindesk.com",
                        "metadata": {
                            "title": "DeFi article",
                            "category": "defi",
                            "rating": {
                                "quality_score": 90,
                                "composite": 90,
                                "tier": "excellent",
                                "factuality": "verified",
                            },
                        },
                    },
                    {
                        "id": "b",
                        "source": "coindesk.com",
                        "metadata": {
                            "title": "Security article",
                            "category": "security",
                            "rating": {
                                "quality_score": 85,
                                "composite": 85,
                                "tier": "excellent",
                                "factuality": "verified",
                            },
                        },
                    },
                ],
                None,
            )

    import rugbrain.qdrant_store as qs

    monkeypatch.setattr(qs, "QdrantVectorStore", FakeStore)
    resp = await client.get("/api/v1/news/top", params={"category": "security", "min_quality": 70})
    assert resp.status_code == 200
    articles = resp.json()["articles"]
    assert [a["id"] for a in articles] == ["b"]


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/fear-index
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fear_index_current(client: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    import rugbrain.fear_index as fi

    async def fake_get_fear_index(
            window: str = "24h", force: bool = False
        ) -> Any:
        return {
            "value": 35,
            "classification": "fear",
            "components": {"fng": 35, "social": None, "onchain": None, "news": None},
            "timestamp": "2026-08-04T09:00:00Z",
            "window": window,
            "samples": [],
            "source": "test",
        }

    monkeypatch.setattr(fi, "get_fear_index", fake_get_fear_index)
    resp = await client.get("/api/v1/fear-index", params={"window": "24h"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["value"] == 35
    assert data["classification"] == "fear"
    assert data["window"] == "24h"


@pytest.mark.asyncio
async def test_fear_index_invalid_window_rejected(client: Any) -> None:
    resp = await client.get("/api/v1/fear-index", params={"window": "99m"})
    assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# GET /api/v1/fear-index/history
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fear_index_history(client: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    import rugbrain.fear_index as fi

    async def fake_history(days: int = 30) -> Any:
        return {
            "days": days,
            "points": [
                {"ts": "2026-08-03T09:00:00Z", "value": 30, "classification": "fear"},
                {"ts": "2026-08-04T09:00:00Z", "value": 55, "classification": "neutral"},
            ],
            "source": "test",
        }

    monkeypatch.setattr(fi, "fear_index_history", fake_history)
    resp = await client.get("/api/v1/fear-index/history", params={"days": 30})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["points"]) == 2
    assert data["days"] == 30


@pytest.mark.asyncio
async def test_fear_index_history_rejects_huge_days(client: Any) -> None:
    resp = await client.get("/api/v1/fear-index/history", params={"days": 4000})
    assert resp.status_code == 422
