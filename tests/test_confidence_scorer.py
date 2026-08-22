"""Tests for rugbrain.confidence_scorer — exact math, boundaries, breakdowns.

Written to close the zero-test coverage gap flagged by the mutation baseline
(all 198 mutants were "no tests"). Every value below is hand-computed from the
documented weights: quality .35 / count .20 / freshness .15 / similarity .15 /
reranker .15.
"""

import math
import time
from typing import Any

from rugbrain.confidence_scorer import SOURCE_QUALITY, score_confidence


def _src(type_: str, **extra: Any) -> dict[str, Any]:
    d = {"type": type_}
    d.update(extra)
    return d


class TestEmpty:
    def test_no_sources_exact(self) -> None:
        assert score_confidence([]) == {
            "confidence_score": 0.0,
            "confidence_label": "insufficient",
            "breakdown": {"sources_count": 0, "reason": "no_sources"},
        }


class TestQuality:
    def test_known_type_lookup(self) -> None:
        out = score_confidence([_src("coingecko")], query_similarity=0.0, reranker_score=0.0)
        assert out["breakdown"]["source_quality"] == 0.85

    def test_unknown_type_gets_floor(self) -> None:
        out = score_confidence([_src("mystical_oracle")], query_similarity=0.0, reranker_score=0.0)
        assert out["breakdown"]["source_quality"] == SOURCE_QUALITY["unknown"]

    def test_average_across_sources(self) -> None:
        # (1.0 + 0.2) / 2 = 0.6
        out = score_confidence(
            [_src("onchain_rpc"), _src("twitter")], query_similarity=0.0, reranker_score=0.0
        )
        assert out["breakdown"]["source_quality"] == 0.6


class TestCountFactor:
    def test_sqrt_diminishing_returns(self) -> None:
        # 1 source @ threshold 3 -> sqrt(1/3)
        out = score_confidence([_src("onchain_rpc")], query_similarity=0.0, reranker_score=0.0)
        assert out["breakdown"]["source_count_factor"] == round(math.sqrt(1 / 3), 4)

    def test_saturates_at_threshold(self) -> None:
        out = score_confidence([_src("onchain_rpc")] * 5, query_similarity=0.0, reranker_score=0.0)
        assert out["breakdown"]["source_count_factor"] == 1.0


class TestFreshness:
    def test_no_timestamps_default_half(self) -> None:
        out = score_confidence([_src("onchain_rpc")], query_similarity=0.0, reranker_score=0.0)
        assert out["breakdown"]["freshness"] == 0.5

    def test_current_timestamp_full_freshness(self) -> None:
        out = score_confidence(
            [_src("onchain_rpc", timestamp=time.time())],
            query_similarity=0.0,
            reranker_score=0.0,
        )
        assert out["breakdown"]["freshness"] == 1.0

    def test_two_half_lives_quarter(self) -> None:
        ts = time.time() - (168.0 * 3600 * 2)  # two half-lives ago
        out = score_confidence(
            [_src("onchain_rpc", timestamp=ts)], query_similarity=0.0, reranker_score=0.0
        )
        assert abs(out["breakdown"]["freshness"] - 0.25) < 0.001

    def test_future_timestamp_clamped_to_one(self) -> None:
        out = score_confidence(
            [_src("onchain_rpc", timestamp=time.time() + 999_999)],
            query_similarity=0.0,
            reranker_score=0.0,
        )
        assert out["breakdown"]["freshness"] == 1.0


class TestClamps:
    def test_similarity_clamped_high(self) -> None:
        out = score_confidence([_src("onchain_rpc")], query_similarity=5.0, reranker_score=0.0)
        assert out["breakdown"]["query_similarity"] == 1.0

    def test_reranker_none_defaults_half(self) -> None:
        out = score_confidence([_src("onchain_rpc")], query_similarity=0.0)
        assert out["breakdown"]["reranker_score"] == 0.5

    def test_reranker_clamped_low(self) -> None:
        out = score_confidence([_src("onchain_rpc")], query_similarity=0.0, reranker_score=-3.0)
        assert out["breakdown"]["reranker_score"] == 0.0


class TestLabels:
    def test_high(self) -> None:
        # 3x onchain, sim 1, rerank 1: .35+.20+.075+.15+.15 = 0.925
        out = score_confidence([_src("onchain_rpc")] * 3, query_similarity=1.0, reranker_score=1.0)
        assert out["confidence_label"] == "high"
        assert out["confidence_score"] == 0.925

    def test_medium(self) -> None:
        # 3x coingecko, sim 1, rerank 0: .2975+.20+.075+.15+0 = 0.7225
        out = score_confidence([_src("coingecko")] * 3, query_similarity=1.0, reranker_score=0.0)
        assert out["confidence_label"] == "medium"
        assert out["confidence_score"] == 0.7225

    def test_low(self) -> None:
        # 3x twitter, sim 1, rerank 0: .07+.20+.075+.15+0 = 0.495
        out = score_confidence([_src("twitter")] * 3, query_similarity=1.0, reranker_score=0.0)
        assert out["confidence_label"] == "low"
        assert out["confidence_score"] == 0.495

    def test_insufficient(self) -> None:
        # reddit x1 @ threshold 10, everything cold: .0225+.0632+0+0+0 = 0.0857
        out = score_confidence(
            [_src("reddit")],
            query_similarity=0.0,
            reranker_score=0.0,
            source_count_threshold=10,
        )
        assert out["confidence_label"] == "insufficient"
        assert 0.18 <= out["confidence_score"] <= 0.20


class TestRerankerZeroRegression:
    def test_explicit_zero_is_not_missing(self) -> None:
        """Regression: `reranker_score or 0.5` used to swallow explicit 0.0."""
        out = score_confidence([_src("onchain_rpc")], query_similarity=0.0, reranker_score=0.0)
        assert out["breakdown"]["reranker_score"] == 0.0

    def test_none_still_defaults_half(self) -> None:
        out = score_confidence([_src("onchain_rpc")], query_similarity=0.0, reranker_score=None)
        assert out["breakdown"]["reranker_score"] == 0.5
