"""Tests for Ground News-style ratings integration in the RAG pipeline."""

import asyncio

import pytest


def _rated_doc(doc_id, score, rating=None, score_field="similarity"):
    doc = {
        "id": doc_id,
        "content": f"Article {doc_id} about a crypto event",
        score_field: score,
    }
    if rating is not None:
        doc["metadata"] = {"rating": rating}
    return doc


def _verified_doc(doc_id, score):
    return _rated_doc(
        doc_id,
        score,
        {
            "factuality": "verified",
            "source_reputation": "established",
            "market_impact": "high",
            "corroborating_sources": 4,
            "on_chain_evidence": True,
        },
    )


def _rumor_doc(doc_id, score):
    return _rated_doc(
        doc_id,
        score,
        {
            "factuality": "rumor",
            "source_reputation": "independent",
            "corroborating_sources": 0,
        },
    )


def _unrated_doc(doc_id, score):
    return _rated_doc(doc_id, score)


# ═══════════════════════════════════════════════════════════════════
# rating_multiplier
# ═══════════════════════════════════════════════════════════════════


def test_rating_multiplier_empty_rating_is_neutral():
    from rugbrain.ratings import rating_multiplier

    assert rating_multiplier({}) == 1.0
    assert rating_multiplier({"market_impact": "medium"}) == 1.0


def test_rating_multiplier_verified_stacked():
    from rugbrain.ratings import rating_multiplier

    rating = {
        "factuality": "verified",  # 1.2
        "source_reputation": "established",  # 1.1
        "market_impact": "high",  # 1.3
        "corroborating_sources": 4,  # 1.15
        "on_chain_evidence": True,  # 1.25
    }
    expected = 1.2 * 1.1 * 1.3 * 1.15 * 1.25
    assert rating_multiplier(rating) == pytest.approx(expected, rel=1e-6)


def test_rating_multiplier_rumor_penalty():
    from rugbrain.ratings import rating_multiplier

    rating = {
        "factuality": "rumor",  # 0.5
        "corroborating_sources": 0,  # 0.8
    }
    assert rating_multiplier(rating) == pytest.approx(0.4, rel=1e-6)


# ═══════════════════════════════════════════════════════════════════
# apply_rating_boosts
# ═══════════════════════════════════════════════════════════════════


def test_apply_rating_boosts_boosts_verified():
    from rugbrain.ratings import apply_rating_boosts

    docs = [_verified_doc("1", 0.5)]
    boosted = apply_rating_boosts(docs)
    assert boosted[0]["similarity"] > 0.5
    assert boosted[0]["rating_boosted"] is True
    assert "rating_multiplier" in boosted[0]


def test_apply_rating_boosts_penalizes_rumor():
    from rugbrain.ratings import apply_rating_boosts

    docs = [_rumor_doc("2", 0.8)]
    boosted = apply_rating_boosts(docs)
    assert boosted[0]["similarity"] < 0.8
    assert boosted[0]["rating_multiplier"] == pytest.approx(0.4, rel=1e-6)


def test_apply_rating_boosts_skips_unrated():
    from rugbrain.ratings import apply_rating_boosts

    docs = [_unrated_doc("3", 0.6)]
    boosted = apply_rating_boosts(docs)
    assert boosted[0]["similarity"] == 0.6
    assert "rating_boosted" not in boosted[0]


def test_apply_rating_boosts_handles_rerank_score_fields():
    """Boosts the first present score field — combined_score for reranked docs."""
    from rugbrain.ratings import apply_rating_boosts

    doc = _verified_doc("4", 0.5)
    doc["cross_score"] = 0.4
    doc["combined_score"] = 0.45
    boosted = apply_rating_boosts([doc])
    # combined_score is first in priority order and must be boosted
    assert boosted[0]["combined_score"] > 0.45


# ═══════════════════════════════════════════════════════════════════
# filter_docs_by_ratings
# ═══════════════════════════════════════════════════════════════════


def test_filter_factuality_verified():
    from rugbrain.ratings import filter_docs_by_ratings

    docs = [_verified_doc("1", 0.9), _rumor_doc("2", 0.8), _unrated_doc("3", 0.7)]
    filtered = filter_docs_by_ratings(docs, factuality_filter="verified")
    ids = [d["id"] for d in filtered]
    assert "1" in ids
    assert "2" not in ids  # rumor conflicts with verified
    assert "3" in ids  # unrated passes through


def test_filter_min_source_tier():
    from rugbrain.ratings import filter_docs_by_ratings

    docs = [_verified_doc("1", 0.9), _rumor_doc("2", 0.8)]
    filtered = filter_docs_by_ratings(docs, min_source_tier="established")
    assert [d["id"] for d in filtered] == ["1"]


def test_filter_require_corroboration():
    from rugbrain.ratings import filter_docs_by_ratings

    docs = [_verified_doc("1", 0.9), _rumor_doc("2", 0.8), _unrated_doc("3", 0.7)]
    filtered = filter_docs_by_ratings(docs, require_corroboration=True)
    ids = [d["id"] for d in filtered]
    assert "1" in ids
    assert "2" not in ids  # 0 corroborating sources
    assert "3" in ids  # unrated passes through


def test_filter_no_criteria_returns_all():
    from rugbrain.ratings import filter_docs_by_ratings

    docs = [_verified_doc("1", 0.9), _rumor_doc("2", 0.8)]
    assert len(filter_docs_by_ratings(docs)) == 2


# ═══════════════════════════════════════════════════════════════════
# format_rag_answer_with_ratings / source badges
# ═══════════════════════════════════════════════════════════════════


def test_source_badge_verified_with_corroboration():
    from rugbrain.ratings import source_badge

    source = {
        "source": "CoinDesk",
        "metadata": {
            "rating": {
                "factuality": "verified",
                "corroborating_sources": 3,
            }
        },
    }
    assert "CoinDesk" in source_badge(source)
    assert "Verified" in source_badge(source)
    assert "3 sources corroborate" in source_badge(source)


def test_format_rag_answer_with_ratings_badges_and_disclaimer():
    from rugbrain.ratings import format_rag_answer_with_ratings

    sources = [
        {
            "source": "CoinDesk",
            "metadata": {"rating": {"factuality": "verified", "corroborating_sources": 3}},
        },
        {
            "source": "RumorPost",
            "metadata": {"rating": {"factuality": "rumor", "corroborating_sources": 0}},
        },
    ]
    out = format_rag_answer_with_ratings("The protocol was exploited.", sources)
    assert "CoinDesk" in out
    assert "RumorPost" in out
    assert "3 sources corroborate" in out
    assert "Disclaimer" in out  # rumor source triggers disclaimer


def test_format_rag_answer_with_ratings_no_sources():
    from rugbrain.ratings import format_rag_answer_with_ratings

    assert format_rag_answer_with_ratings("plain answer", []) == "plain answer"


# ═══════════════════════════════════════════════════════════════════
# prioritize_verified_sources / synthesize_answer
# ═══════════════════════════════════════════════════════════════════


def test_prioritize_verified_sources():
    from rugbrain.ratings import prioritize_verified_sources

    sources = [
        _rumor_doc("rumor", 0.9),
        _verified_doc("verified", 0.8),
        _unrated_doc("unrated", 0.7),
    ]
    ordered = prioritize_verified_sources(sources)
    assert ordered[0]["id"] == "verified"
    assert ordered[1]["id"] == "unrated"
    assert ordered[2]["id"] == "rumor"


def test_synthesize_answer_shape():
    from rugbrain.ratings import synthesize_answer

    result = synthesize_answer(
        "The protocol was exploited.",
        [_verified_doc("1", 0.9), _rumor_doc("2", 0.8)],
    )
    assert "answer" in result
    assert "citations" in result
    assert "rating_summary" in result
    assert result["rating_summary"]["verified_count"] == 1
    assert result["rating_summary"]["rumor_count"] == 1
    assert result["rating_summary"]["max_corroboration"] == 4
    assert "Disclaimer" in result["answer"]


# ═══════════════════════════════════════════════════════════════════
# Pipeline integration
# ═══════════════════════════════════════════════════════════════════


def test_synthesize_answer_node():
    """synthesize_answer (shared rating node) surfaces verified sources first."""
    from rugbrain.ratings import synthesize_answer

    result = synthesize_answer("answer", [_verified_doc("1", 0.9)])
    assert result["rating_summary"]["verified_count"] == 1
    assert result["citations"][0]["id"] == "1"


def test_reranker_applies_rating_boosts_on_top_of_cross_encoder():
    """rerank() re-sorts so a verified doc outranks a raw semantic match."""
    from rugbrain.reranker import Reranker

    reranker = Reranker(use_infinity=False)
    reranker._st_loaded = True

    # Raw cross-encoder scores: doc2 slightly more relevant than doc1
    def fake_score(query, docs):
        return [0.6, 0.65]

    reranker._score_st = fake_score

    async def run():
        return await reranker.rerank(
            "hack news",
            [
                _verified_doc("verified", 0.5),
                _rumor_doc("rumor", 0.7),
            ],
            top_k=2,
        )

    results = asyncio.run(run())

    # After rating boosts, verified doc (boosted) ranks above rumor doc
    assert results[0]["id"] == "verified"
    assert results[0]["rating_boosted_after_rerank"] is True
    assert results[0]["combined_score"] > 0.6


def test_reranker_skip_ratings_flag():
    """apply_ratings=False leaves pure cross-encoder ordering."""
    from rugbrain.reranker import Reranker

    reranker = Reranker(use_infinity=False)
    reranker._st_loaded = True

    def fake_score(query, docs):
        return [0.6, 0.65]

    reranker._score_st = fake_score

    async def run():
        return await reranker.rerank(
            "hack news",
            [_verified_doc("verified", 0.5), _rumor_doc("rumor", 0.7)],
            top_k=2,
            apply_ratings=False,
        )

    results = asyncio.run(run())
    assert results[0]["id"] == "rumor"
    assert "rating_boosted_after_rerank" not in results[0]


def test_search_endpoint_has_rating_filter_params():
    """GET /api/v1/search accepts the new rating filter query params."""
    import inspect

    from rugbrain.endpoints import search_threat_intel

    sig = inspect.signature(search_threat_intel)
    assert "factuality_filter" in sig.parameters
    assert "min_source_tier" in sig.parameters
    assert "require_corroboration" in sig.parameters


def test_three_pillar_search_has_rating_boost_param():
    """three_pillar_search accepts use_rating_boosts."""
    import inspect

    from rugbrain.search.engine import three_pillar_search

    sig = inspect.signature(three_pillar_search)
    assert "use_rating_boosts" in sig.parameters
