"""Tests for story bias/sentiment/factual-consensus computation.

Part 3 of the news aggregation system: per-story bias distribution,
sentiment distribution, and factual consensus derived from the
news_sources.py source-credibility database.
"""

from __future__ import annotations

from rugbrain import news_clustering as nc
from rugbrain.news_sources import EXTENDED_SOURCE_LEANINGS


def _articles():
    return [
        {
            "id": "b1",
            "title": "Ethereum ETF approval news roundup",
            "url": "https://nytimes.com/etf",
            "source": "nytimes.com",
            "summary": "The SEC approved spot ether ETFs today.",
            "published_at": "2026-08-05T09:00:00Z",
            "category": "ethereum",
        },
        {
            "id": "b2",
            "title": "Ethereum ETF gets SEC green light",
            "url": "https://reuters.com/etf",
            "source": "reuters.com",
            "summary": "SEC approved spot ether ETFs, market reacts.",
            "published_at": "2026-08-05T10:00:00Z",
            "category": "ethereum",
        },
        {
            "id": "b3",
            "title": "Ethereum ETF approved — market reacts",
            "url": "https://coindesk.com/etf",
            "source": "coindesk.com",
            "summary": "Regulators approved ether ETFs; prices moved higher.",
            "published_at": "2026-08-05T11:00:00Z",
            "category": "ethereum",
        },
    ]


def test_source_leanings_database_populated():
    assert len(EXTENDED_SOURCE_LEANINGS) >= 100
    # Spot checks on the major outlets.
    assert EXTENDED_SOURCE_LEANINGS["reuters.com"] == "center"
    assert EXTENDED_SOURCE_LEANINGS["nytimes.com"] == "left"
    assert EXTENDED_SOURCE_LEANINGS["wsj.com"] == "right"
    assert EXTENDED_SOURCE_LEANINGS["coindesk.com"] == "center"


def test_bias_distribution_counts_known_leanings():
    story = nc.build_story(_articles())
    dist = story.bias_distribution
    # nytimes -> left, reuters -> center, coindesk -> center.
    assert dist["left"] == 1
    assert dist["center"] == 2
    assert dist["right"] == 0
    assert dist["unknown"] == 0


def test_bias_distribution_unknown_source_counts_unknown():
    articles = [
        {
            "id": "u1",
            "title": "Anonymous blog post",
            "url": "https://random-blog.example/x",
            "source": "random-blog.example",
            "summary": "Some unverified claims about a token.",
            "published_at": "2026-08-05T09:00:00Z",
        }
    ]
    story = nc.build_story(articles)
    assert story.bias_distribution["unknown"] == 1


def test_sentiment_distribution_counts_positive_negative():
    articles = [
        {
            "id": "s1",
            "title": "Token moons after listing",
            "url": "https://coindesk.com/moon",
            "source": "coindesk.com",
            "summary": "The token pumped to an all time high and everyone is bullish.",
            "published_at": "2026-08-05T09:00:00Z",
        },
        {
            "id": "s2",
            "title": "Token rug pulled",
            "url": "https://cointelegraph.com/rug",
            "source": "cointelegraph.com",
            "summary": "Scam alert: the team dumped the token and scammed investors.",
            "published_at": "2026-08-05T10:00:00Z",
        },
    ]
    story = nc.build_story(articles)
    dist = story.sentiment_distribution
    assert dist["positive"] >= 1
    assert dist["negative"] >= 1


def test_factual_consensus_high_on_shared_claims():
    articles = [
        {
            "id": "f1",
            "title": "Exchange loses $12.4 million to exploit",
            "url": "https://coindesk.com/exploit",
            "source": "coindesk.com",
            "summary": (
                "The exchange lost $12.4 million when an attacker drained the "
                "liquidity pool. Address 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
                "was involved and the price fell 34%."
            ),
            "published_at": "2026-08-05T09:00:00Z",
        },
        {
            "id": "f2",
            "title": "Exchange drained of $12.4 million",
            "url": "https://theblock.co/exploit",
            "source": "theblock.co",
            "summary": (
                "Attackers stole $12.4 million from the exchange's liquidity "
                "pool. Funds went to 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
                "and the token dropped 34%."
            ),
            "published_at": "2026-08-05T10:00:00Z",
        },
    ]
    story = nc.build_story(articles)
    # Amount, address and percentage overlap across both sources.
    assert story.factual_consensus >= 0.5
    assert story.consensus_detail["total_unique_claims"] > 0


def test_consensus_detail_has_metrics():
    story = nc.build_story(_articles())
    detail = story.consensus_detail
    assert "total_unique_claims" in detail
    assert "multi_source_claims" in detail
    assert "single_source_claims" in detail


def test_timeline_sorted_by_published():
    story = nc.build_story(_articles())
    times = [item["published_at"] for item in story.timeline]
    assert times == sorted(times)
