"""Tests for the Ground News-style multi-axis news rating engine."""

import pytest

from rugbrain import news_rating as nr
from rugbrain.ratings import quality_multiplier, rating_multiplier


def _factual_text() -> str:
    """Dense, verifiable article body — addresses, amounts, protocols."""
    return (
        "Uniswap lost $12.4 million when an attacker drained the liquidity "
        "pool via a reentrancy exploit. The attacker address "
        "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa moved 2,500 ETH to a "
        "fresh wallet and the token price fell 34%. Aave and Compound "
        "positions were liquidated as collateral dropped, and the Curve "
        "governance vote was postponed."
    )


def _hype_text() -> str:
    return (
        "MOON! This token is going to the MOON!!! Everyone buy NOW, do not "
        "miss out on this once in a lifetime 100x GEM. The team is INSANE "
        "and this will change EVERYTHING. TO THE MOON!!! LAMBO soon!!!"
    )


def _neutral_text() -> str:
    return (
        "The protocol published a post-mortem today. According to the team, "
        "the exploit affected 12% of deposits; however, analysts say the "
        "impact on the broader market was limited. The exchange declined to "
        "comment on the timeline."
    )


# ═══════════════════════════════════════════════════════════════════════
# Source credibility
# ═══════════════════════════════════════════════════════════════════════


def test_source_reputation_established_scores_high():
    assert nr.source_reputation_score("", "https://coindesk.com/article/1") >= 90
    assert nr.source_reputation_score("", "https://www.theblock.co/x") >= 90
    assert nr.source_reputation_score("", "https://decrypt.co/1") >= 85


def test_source_reputation_unknown_scores_low():
    assert nr.source_reputation_score("", "https://random-blog.example/post") == 45


def test_source_reputation_name_alias():
    assert nr.source_reputation_score("CoinDesk") == 95
    assert nr.source_reputation_score("coinjournal") == 62


def test_source_reputation_override_wins():
    assert nr.source_reputation_score("random", "https://x.example", override=80) == 80


def test_source_reputation_label():
    assert nr.source_reputation_label(80) == "established"
    assert nr.source_reputation_label(55) == "independent"
    assert nr.source_reputation_label(30) == "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Factual density
# ═══════════════════════════════════════════════════════════════════════


def test_factual_density_dense_article_scores_high():
    assert nr.factual_density_score(_factual_text()) >= 60


def test_factual_density_opinion_fluff_scores_low():
    fluff = (
        "This is a wonderful project with an incredible vision. The team is "
        "amazing and the future is bright. Everyone should feel great about "
        "this once in a lifetime opportunity to be early."
    )
    assert nr.factual_density_score(fluff) < 40


def test_factual_density_empty_text():
    assert nr.factual_density_score("") == 0
    assert nr.factual_density_score("short") == 0


def test_factual_density_claims_are_deduplicated():
    claims = nr._find_claims(_factual_text())
    assert "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in claims["addresses"]
    assert "uniswap" in claims["protocols"]
    assert claims["amounts_usd"]


# ═══════════════════════════════════════════════════════════════════════
# Timeliness
# ═══════════════════════════════════════════════════════════════════════


def test_timeliness_fresh_scores_high():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert nr.timeliness_score(now, now=now) >= 88
    assert nr.timeliness_score(now - timedelta(hours=2), now=now) >= 88


def test_timeliness_old_scores_low():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert nr.timeliness_score(now - timedelta(days=40), now=now) < 50


def test_timeliness_missing_is_neutral():
    assert nr.timeliness_score(None) == 50


def test_timeliness_event_lag_penalty():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    published = now - timedelta(days=5)
    old_event = now - timedelta(days=20)
    with_lag = nr.timeliness_score(published, event_at=old_event, now=now)
    without_lag = nr.timeliness_score(published, now=now)
    assert with_lag < without_lag


# ═══════════════════════════════════════════════════════════════════════
# Bias detection
# ═══════════════════════════════════════════════════════════════════════


def test_bias_neutral_scores_high():
    assert nr.bias_score(_neutral_text()) >= 75


def test_bias_hype_scores_low():
    assert nr.bias_score(_hype_text()) < 45


def test_bias_short_text_neutral_default():
    assert nr.bias_score("hi") == 50


# ═══════════════════════════════════════════════════════════════════════
# Corroboration
# ═══════════════════════════════════════════════════════════════════════


def test_corroboration_score_mapping():
    assert nr.corroboration_score(None) == 30
    assert nr.corroboration_score(0) == 30
    assert nr.corroboration_score(1) == 45
    assert nr.corroboration_score(3) == 75
    assert nr.corroboration_score(4) == 85
    assert nr.corroboration_score(5) == 92


# ═══════════════════════════════════════════════════════════════════════
# Technical depth
# ═══════════════════════════════════════════════════════════════════════


def test_technical_depth_mechanics_scores_high():
    assert nr.technical_depth_score(_factual_text()) >= 60


def test_technical_depth_surface_level_low():
    surface = "Token went up. It is a good coin. Many people bought it."
    assert nr.technical_depth_score(surface) < 40


# ═══════════════════════════════════════════════════════════════════════
# Source diversity
# ═══════════════════════════════════════════════════════════════════════


def test_source_diversity_single_vs_multi():
    assert nr.source_diversity_score({}) == 50
    assert nr.source_diversity_score({"source_urls": ["https://a.example"]}) == 40
    multi = {"source_urls": ["https://a.example", "https://b.example", "https://c.example"]}
    assert nr.source_diversity_score(multi) == 75


def test_source_diversity_aggregated_counts_as_multi():
    assert nr.source_diversity_score({"aggregated": True}) >= 75


# ═══════════════════════════════════════════════════════════════════════
# Composite + full pipeline
# ═══════════════════════════════════════════════════════════════════════


def test_composite_is_weighted_average():
    article = {
        "url": "https://coindesk.com/article/1",
        "source": "CoinDesk",
        "title": "A title",
        "content": _factual_text(),
        "published_at": "2026-08-04T09:00:00Z",
        "corroborating_sources": 3,
        "source_urls": ["https://a.example", "https://b.example"],
    }
    rating = nr.rate_article(article)
    expected = round(sum(nr.AXIS_WEIGHTS[a] * rating.axes[a] for a in nr.AXES))
    assert rating.composite == expected
    assert 0 <= rating.composite <= 100


def test_rate_article_axes_shape():
    rating = nr.rate_article(
        {
            "url": "https://coindesk.com/article/2",
            "title": "Exploit post-mortem",
            "content": _factual_text(),
            "published_at": "2026-08-04T09:00:00Z",
            "corroborating_sources": 4,
        }
    )
    assert set(rating.axes.keys()) == set(nr.AXES)
    for _axis, value in rating.axes.items():
        assert 0 <= value <= 100
    assert rating.factuality in ("verified", "reported", "rumor")
    assert rating.source_reputation in ("established", "independent", "unknown")


def test_rate_article_poor_opinion_piece():
    rating = nr.rate_article(
        {
            "url": "https://random-blog.example/post",
            "title": "Buy this coin",
            "content": _hype_text(),
            "published_at": None,
            "corroborating_sources": 0,
        }
    )
    assert rating.tier == "poor"
    assert rating.factuality == "rumor"
    assert rating.source_reputation == "unknown"
    assert rating.composite < 50


def test_to_payload_includes_legacy_and_quality():
    rating = nr.rate_article(
        {
            "url": "https://coindesk.com/article/3",
            "content": _factual_text(),
            "corroborating_sources": 4,
        }
    )
    payload = rating.to_payload()
    assert payload["quality_score"] == rating.composite
    assert payload["composite"] == rating.composite
    assert "axes" in payload
    assert "factuality" in payload
    assert "corroborating_sources" in payload
    assert "on_chain_evidence" in payload


def test_rate_article_from_rating_dict_round_trip():
    rating = nr.rate_article({"url": "https://coindesk.com/x", "content": _factual_text()})
    payload = nr.rate_article_from_rating_dict(rating.to_payload())
    assert payload["composite"] == rating.composite
    assert payload["quality_score"] == rating.composite


def test_on_chain_evidence_detected_from_addresses():
    rating = nr.rate_article(
        {
            "url": "https://coindesk.com/x",
            "content": (
                "An address 0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa drained "
                "the protocol. The transaction 0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb was traced."
            ),
        }
    )
    assert rating.on_chain_evidence is True


# ═══════════════════════════════════════════════════════════════════════
# Search boost integration (ratings.quality_multiplier)
# ═══════════════════════════════════════════════════════════════════════


def test_quality_multiplier_bands():
    assert quality_multiplier(None) == 1.0
    assert quality_multiplier(90) == 1.15
    assert quality_multiplier(75) == 1.08
    assert quality_multiplier(60) == 1.0
    assert quality_multiplier(45) == 0.9
    assert quality_multiplier(20) == 0.75


def test_rating_multiplier_applies_quality():
    assert rating_multiplier({"quality_score": 90}) == pytest.approx(1.15, rel=1e-6)
    assert rating_multiplier({"quality_score": 20}) == pytest.approx(0.75, rel=1e-6)
    assert rating_multiplier({}) == 1.0


def test_apply_rating_boosts_uses_quality():
    from rugbrain.ratings import apply_rating_boosts

    doc = {
        "id": "1",
        "content": "article",
        "similarity": 0.5,
        "metadata": {"rating": {"quality_score": 90, "factuality": "verified"}},
    }
    boosted = apply_rating_boosts([doc])
    assert boosted[0]["similarity"] > 0.5
    assert boosted[0]["rating_multiplier"] == pytest.approx(1.15 * 1.2, rel=1e-6)
