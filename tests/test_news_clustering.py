"""Tests for news story clustering (rugbrain.news_clustering)."""

from __future__ import annotations

import asyncio

import pytest

from rugbrain import news_clustering as nc

# ── Embedding / cosine ────────────────────────────────────────────────────


def test_cosine_similarity_identical_vectors():
    assert nc.cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    assert nc.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_empty_or_mismatched():
    assert nc.cosine_similarity([], [1.0, 2.0]) == 0.0
    assert nc.cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    assert nc.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# ── Clustering ────────────────────────────────────────────────────────────


def test_cluster_embeddings_groups_similar():
    """Similar vectors cluster together; distant vectors form new clusters."""
    near_a = [1.0, 0.0, 0.0]
    near_b = [0.95, 0.05, 0.0]
    far = [0.0, 1.0, 0.0]
    clusters = nc.cluster_embeddings([near_a, far, near_b], threshold=0.8)
    assert len(clusters) == 2
    # Indices 0 and 2 share a cluster; index 1 is alone.
    groups = [set(c) for c in clusters]
    assert {0, 2} in groups
    assert {1} in groups


def test_cluster_embeddings_all_similar_single_cluster():
    vectors = [[1.0, 0.0], [0.99, 0.01], [0.98, 0.02]]
    clusters = nc.cluster_embeddings(vectors, threshold=0.9)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_cluster_embeddings_high_threshold_splits():
    vectors = [[1.0, 0.0], [0.8, 0.6], [0.0, 1.0]]
    clusters = nc.cluster_embeddings(vectors, threshold=0.95)
    assert len(clusters) == 3  # nothing meets the strict threshold


# ── Story building ────────────────────────────────────────────────────────


def _articles():
    return [
        {
            "id": "a1",
            "title": "Ethereum ETF approval news roundup",
            "url": "https://coindesk.com/etf-roundup",
            "source": "coindesk.com",
            "summary": "The SEC approved spot ether ETFs today after a long review.",
            "published_at": "2026-08-05T09:00:00Z",
            "category": "ethereum",
        },
        {
            "id": "a2",
            "title": "Ethereum ETF gets SEC green light",
            "url": "https://theblock.co/etf-greenlight",
            "source": "theblock.co",
            "summary": "SEC approved spot ether ETFs; the market responded positively.",
            "published_at": "2026-08-05T10:00:00Z",
            "category": "ethereum",
        },
        {
            "id": "a3",
            "title": "Bitcoin rallies past 120k",
            "url": "https://cointelegraph.com/btc-120k",
            "source": "cointelegraph.com",
            "summary": "Bitcoin broke resistance at 120k as demand surged.",
            "published_at": "2026-08-05T09:30:00Z",
            "category": "bitcoin",
        },
    ]


def test_build_story_sources_and_credibility():
    story = nc.build_story(_articles())
    assert len(story.sources) == 3
    # coindesk + theblock are established (>=90), cointelegraph >= 85.
    creds = {s.credibility for s in story.sources}
    assert all(c >= 85 for c in creds)
    assert any(s.domain == "coindesk.com" for s in story.sources)


def test_build_story_timeline_sorted_ascending():
    story = nc.build_story(_articles())
    times = [nc._parse_dt(item["published_at"]) for item in story.timeline]
    assert times == sorted(times)
    assert len(story.timeline) == 3


def test_build_story_sentiment_distribution_shape():
    story = nc.build_story(_articles())
    for key in ("positive", "neutral", "negative"):
        assert key in story.sentiment_distribution
    assert sum(story.sentiment_distribution.values()) == 3


def test_build_story_bias_distribution_shape():
    story = nc.build_story(_articles())
    for key in ("left", "center", "right", "unknown"):
        assert key in story.bias_distribution
    # Crypto-native outlets are all labeled center.
    assert story.bias_distribution["center"] == 3


def test_build_story_topics_extracted():
    story = nc.build_story(_articles())
    assert "ethereum" in story.topics
    assert "bitcoin" in story.topics


def test_build_story_deterministic_id():
    a = _articles()
    b = _articles()
    assert nc.build_story(a).id == nc.build_story(b).id


def test_build_story_summary_nonempty():
    story = nc.build_story(_articles())
    assert story.summary.strip()


def test_build_story_factual_consensus_agreement():
    """Sources sharing the same verifiable claims get high consensus."""
    articles = [
        {
            "id": "c1",
            "title": "Exchange loses $12.4 million to exploit",
            "url": "https://coindesk.com/exploit",
            "source": "coindesk.com",
            "summary": (
                "The exchange lost $12.4 million when an attacker drained the "
                "liquidity pool via a reentrancy exploit. The attacker address "
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa moved the funds."
            ),
            "published_at": "2026-08-05T09:00:00Z",
        },
        {
            "id": "c2",
            "title": "Exchange drained of $12.4 million",
            "url": "https://theblock.co/exploit",
            "source": "theblock.co",
            "summary": (
                "Attackers stole $12.4 million from the exchange using a "
                "reentrancy attack on the liquidity pool. Funds moved to "
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa."
            ),
            "published_at": "2026-08-05T10:00:00Z",
        },
    ]
    story = nc.build_story(articles)
    # Amount ($12.4m) + address + protocol claims overlap across both sources.
    assert story.factual_consensus >= 0.5


def test_build_story_factual_consensus_low_when_disjoint():
    """Sources with no shared claims get low/neutral consensus."""
    articles = [
        {
            "id": "d1",
            "title": "Token launches new mainnet",
            "url": "https://coindesk.com/mainnet",
            "source": "coindesk.com",
            "summary": "The team announced the mainnet launch date.",
            "published_at": "2026-08-05T09:00:00Z",
        },
        {
            "id": "d2",
            "title": "Team hires new CTO",
            "url": "https://theblock.co/cto",
            "source": "theblock.co",
            "summary": "The company announced a new chief technology officer.",
            "published_at": "2026-08-05T10:00:00Z",
        },
    ]
    story = nc.build_story(articles)
    assert 0.0 <= story.factual_consensus <= 1.0


# ── Orchestration with injected embedder ─────────────────────────────────


async def _fake_embedder(titles: list[str]) -> list[list[float]]:
    """Deterministic title-embedder keyed on the first word of the title.

    Titles that start with the same token (e.g. "Ethereum ETF ..." vs
    "Ethereum ETF ...") produce identical unit vectors, guaranteeing high
    cosine similarity; different first tokens produce orthogonal vectors.
    """
    vecs: list[list[float]] = []
    for title in titles:
        key = (title or "").lower().split()[0] if (title or "").strip() else ""
        if key.startswith("ethereum"):
            vecs.append([1.0, 0.0, 0.0, 0.0])
        elif key.startswith("bitcoin"):
            vecs.append([0.0, 1.0, 0.0, 0.0])
        else:
            vecs.append([0.0, 0.0, 1.0, 0.0])
    return vecs


@pytest.mark.asyncio
async def test_cluster_articles_groups_related():
    articles = [
        {
            "id": "1",
            "title": "Ethereum ETF approval news roundup",
            "url": "https://coindesk.com/1",
            "source": "coindesk.com",
            "summary": "SEC approved ether ETFs.",
            "published_at": "2026-08-05T09:00:00Z",
            "category": "ethereum",
        },
        {
            "id": "2",
            "title": "Ethereum ETF gets SEC green light",
            "url": "https://theblock.co/2",
            "source": "theblock.co",
            "summary": "SEC approved spot ether ETFs.",
            "published_at": "2026-08-05T10:00:00Z",
            "category": "ethereum",
        },
        {
            "id": "3",
            "title": "Bitcoin price surge continues",
            "url": "https://cointelegraph.com/3",
            "source": "cointelegraph.com",
            "summary": "BTC up 5%.",
            "published_at": "2026-08-05T11:00:00Z",
            "category": "bitcoin",
        },
    ]
    stories = await nc.cluster_articles(articles, threshold=0.5, embedder=_fake_embedder)
    assert len(stories) == 2
    # The two Ethereum articles cluster together.
    eth = next(s for s in stories if s.title.startswith("Ethereum"))
    assert {src.article_id for src in eth.sources} == {"1", "2"}


@pytest.mark.asyncio
async def test_cluster_articles_empty_input():
    assert await nc.cluster_articles([], embedder=_fake_embedder) == []


def test_embed_titles_uses_embeddings_module(monkeypatch):
    """embed_titles delegates to rugbrain.embeddings.get_embeddings."""
    calls: list[list[str]] = []

    async def fake_get_embeddings(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[1.0] * 4 for _ in texts]

    import rugbrain.embeddings as emb

    monkeypatch.setattr(emb, "get_embeddings", fake_get_embeddings)

    async def run():
        return await nc.embed_titles(["hello", "world"])

    result = asyncio.run(run())
    assert len(result) == 2
    assert calls == [["hello", "world"]]
