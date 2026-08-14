"""Tests for the Part 3 news-rating expansion: expanded source DB,
cross-referencing, clickbait detection, and on-chain claim verification."""

from __future__ import annotations

import pytest

from rugbrain import news_rating as nr

# ═══════════════════════════════════════════════════════════════════════
# Expanded source credibility database (100+ sources)
# ═══════════════════════════════════════════════════════════════════════


def test_source_database_has_100_plus_entries():
    assert len(nr.SOURCE_REPUTATION) >= 100


def test_source_database_covers_major_outlets():
    for domain in (
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "coindesk.com",
        "theblock.co",
        "cointelegraph.com",
        "ethereum.org",
        "sec.gov",
        "chainalysis.com",
        "defillama.com",
        "rekt.news",
        "revoke.cash",
    ):
        assert domain in nr.SOURCE_REPUTATION, domain


def test_source_database_extended_outlets_score():
    assert nr.source_reputation_score("", "https://theblock.co/x") >= 90
    assert nr.source_reputation_score("", "https://decrypt.co/x") >= 85
    assert nr.source_reputation_score("", "https://l2beat.com/x") >= 80
    assert nr.source_reputation_score("", "https://gov.uk/x") >= 85


def test_source_database_social_scores_low():
    assert nr.source_reputation_score("", "https://reddit.com/r/crypto") < 50
    assert nr.source_reputation_score("", "https://x.com/crypto") < 50


def test_extended_alias_resolves():
    assert nr.source_reputation_score("CNBC") >= 80
    assert nr.source_reputation_score("Messari") >= 80
    assert nr.source_reputation_score("Chainalysis") >= 80


# ═══════════════════════════════════════════════════════════════════════
# Cross-referencing
# ═══════════════════════════════════════════════════════════════════════


def test_cross_reference_count_trusted_domains():
    article = {
        "source_urls": [
            "https://coindesk.com/a",
            "https://theblock.co/b",
            "https://decrypt.co/c",
        ]
    }
    assert nr.cross_reference_count(article) == 3
    assert nr.cross_reference_score(article) == 75


def test_cross_reference_count_unknown_domain_is_weak():
    article = {"source_urls": ["https://random-blog.example/x"]}
    # Single unknown source does not count as corroboration.
    assert nr.cross_reference_count(article) == 1


def test_cross_reference_explicit_wins():
    article = {"corroborating_sources": 4}
    assert nr.cross_reference_count(article) == 4


def test_cross_reference_aggregated_counts():
    article = {"aggregated": True}
    assert nr.cross_reference_count(article) >= 2


# ═══════════════════════════════════════════════════════════════════════
# Clickbait detection
# ═══════════════════════════════════════════════════════════════════════


def test_clickbait_phrase_detected():
    assert nr.clickbait_score("You won't believe what this coin did next!") > 0


def test_clickbait_allcaps_detected():
    assert nr.clickbait_score("BUY NOW!! THIS CHANGES EVERYTHING!!!") > 30


def test_clickbait_emoji_detected():
    assert nr.clickbait_score("🚀🚀🚀 TO THE MOON!!! 🚀🚀🚀") > 20


def test_clickbait_neutral_text_scores_low():
    neutral = (
        "The protocol published a post-mortem today. According to the team, "
        "the exploit affected 12% of deposits; however, analysts say the "
        "impact on the broader market was limited."
    )
    assert nr.clickbait_score(neutral) < 25


def test_clickbait_feeds_bias_axis():
    article = {
        "url": "https://coindesk.com/a",
        "title": "BUY NOW!!!",
        "content": "You won't believe this!!! 🚀🚀 TO THE MOON!!!",
    }
    rating = nr.rate_article(article)
    assert rating.clickbait_score > 0
    # The clickbait penalty drags the composite down relative to the same
    # article without clickbait.
    clean = nr.rate_article(
        {"url": "https://coindesk.com/a", "title": "A", "content": "Neutral report."}
    )
    assert rating.axes["bias"] <= clean.axes["bias"]


# ═══════════════════════════════════════════════════════════════════════
# On-chain claim verification
# ═══════════════════════════════════════════════════════════════════════


def test_on_chain_claim_usd_parsed():
    article = {"content": "The protocol lost $12.4 million when the attacker drained the pool."}
    result = nr.on_chain_claim_verification(article)
    assert result["claims"]
    assert result["claims"][0]["amount_usd"] == pytest.approx(12_400_000, rel=1e-6)


def test_on_chain_claim_token_parsed():
    article = {"content": "2,500 ETH was stolen in the exploit."}
    result = nr.on_chain_claim_verification(article)
    assert any(c.get("token") == "ETH" for c in result["claims"])


def test_on_chain_claim_verified_by_artifact():
    article = {
        "content": (
            "The attacker address 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
            "moved $12.4 million in a reentrancy drain."
        )
    }
    result = nr.on_chain_claim_verification(article)
    assert result["verified"] is True
    assert result["artifact_count"] >= 1
    assert result["claims"][0]["verified"] is True


def test_on_chain_claim_unverified_without_artifact():
    article = {"content": "Reports say the protocol lost $12.4 million."}
    result = nr.on_chain_claim_verification(article)
    assert result["claims"]
    assert result["verified"] is False


def test_rate_article_carries_extended_fields():
    rating = nr.rate_article(
        {
            "url": "https://coindesk.com/x",
            "title": "Exploit post-mortem",
            "content": (
                "The attacker address 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
                "moved $12.4 million in a reentrancy drain of the Uniswap pool."
            ),
            "source_urls": ["https://theblock.co/b", "https://decrypt.co/c"],
        }
    )
    assert rating.cross_referenced >= 2
    assert rating.on_chain_verified is True
    assert rating.on_chain_claims
    payload = rating.to_payload()
    assert "clickbait_score" in payload
    assert "cross_referenced" in payload
    assert "on_chain_verified" in payload


def test_rate_article_from_rating_dict_round_trip_extended():
    rating = nr.rate_article(
        {
            "url": "https://coindesk.com/x",
            "content": "Uniswap lost $12.4 million when an attacker drained the pool.",
            "source_urls": ["https://theblock.co/b"],
        }
    )
    payload = nr.rate_article_from_rating_dict(rating.to_payload())
    assert payload["composite"] == rating.composite
    assert payload["quality_score"] == rating.composite
    assert "clickbait_score" in payload
    assert "cross_referenced" in payload
    assert "on_chain_verified" in payload
