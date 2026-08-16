"""Unit tests for rugbrain.eval.ndcg — ranking metric math.

Known-value vectors verified independently (see module docstring math):
  ndcg(["a","b","c","d"], ["b","d"])        == 0.650921...
  ndcg(["a","b","c","d"], ["b","d"], k=2)   == 0.386853...
"""

from __future__ import annotations

import pytest

from rugbrain.eval.ndcg import (
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


class TestNdcgAtK:
    def test_perfect_ranking(self):
        assert ndcg_at_k(["a", "b"], ["a", "b"]) == pytest.approx(1.0)

    def test_known_value(self):
        # Verified: DCG=1/log2(3)+1/log2(5); IDCG=1/log2(2)+1/log2(3).
        assert ndcg_at_k(["a", "b", "c", "d"], ["b", "d"]) == pytest.approx(0.650921, abs=1e-6)

    def test_cutoff_truncation(self):
        # k=2 only sees "a","b" — one relevant doc at rank 2.
        assert ndcg_at_k(["a", "b", "c", "d"], ["b", "d"], k=2) == pytest.approx(0.386853, abs=1e-6)

    def test_no_hits(self):
        assert ndcg_at_k(["x", "y", "z"], ["q"]) == 0.0

    def test_empty_relevant_set(self):
        assert ndcg_at_k(["a", "b"], []) == 0.0

    def test_empty_retrieved(self):
        assert ndcg_at_k([], ["a"]) == 0.0

    def test_k_larger_than_retrieved_clamps(self):
        assert ndcg_at_k(["a", "b"], ["a", "b"], k=99) == pytest.approx(1.0)

    def test_k_zero(self):
        assert ndcg_at_k(["a", "b"], ["a", "b"], k=0) == 0.0

    def test_relevant_ordering_matters(self):
        # Same set, worse ordering -> lower NDCG.
        good = ndcg_at_k(["a", "b", "c"], ["a", "b"])
        bad = ndcg_at_k(["c", "a", "b"], ["a", "b"])
        assert good > bad


class TestRecallAtK:
    def test_all_found(self):
        assert recall_at_k(["a", "b", "c"], ["a", "b"]) == pytest.approx(1.0)

    def test_partial(self):
        assert recall_at_k(["a", "x", "y"], ["a", "b", "c"]) == pytest.approx(1 / 3)

    def test_cutoff(self):
        # "b" is at rank 4 — outside k=3.
        assert recall_at_k(["a", "x", "y", "b"], ["b"], k=3) == 0.0
        assert recall_at_k(["a", "x", "y", "b"], ["b"], k=4) == pytest.approx(1.0)

    def test_empty_relevant_set(self):
        assert recall_at_k(["a", "b"], []) == 1.0

    def test_none_found(self):
        assert recall_at_k(["x", "y"], ["a"]) == 0.0


class TestHitRateAtK:
    def test_hit(self):
        assert hit_rate_at_k(["a", "b", "c"], ["c"]) == 1.0

    def test_miss(self):
        assert hit_rate_at_k(["a", "b", "c"], ["z"]) == 0.0

    def test_hit_beyond_cutoff(self):
        assert hit_rate_at_k(["a", "b", "c", "z"], ["z"], k=3) == 0.0

    def test_empty_relevant_set(self):
        assert hit_rate_at_k(["a", "b"], []) == 1.0


class TestPrecisionAtK:
    def test_all_relevant(self):
        assert precision_at_k(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)

    def test_partial(self):
        assert precision_at_k(["a", "x", "y", "b"], ["a", "b"], k=4) == pytest.approx(0.5)

    def test_cutoff(self):
        # Only "a" is within k=2; the relevant "b" at rank 4 is excluded.
        assert precision_at_k(["a", "x", "y", "b"], ["a", "b"], k=2) == pytest.approx(0.5)
        assert precision_at_k(["a", "x", "y", "b"], ["a", "b"], k=1) == pytest.approx(1.0)

    def test_no_hits(self):
        assert precision_at_k(["x", "y"], ["a"]) == 0.0

    def test_empty_relevant_set(self):
        # Precision is undefined with no labeled relevant docs -> 0.0
        # (differs from recall/hit-rate which pass on empty sets).
        assert precision_at_k(["a", "b"], []) == 0.0

    def test_empty_retrieved(self):
        assert precision_at_k([], ["a"]) == 0.0

    def test_k_zero(self):
        assert precision_at_k(["a", "b"], ["a"], k=0) == 0.0

    def test_k_larger_than_retrieved_clamps(self):
        assert precision_at_k(["a", "b"], ["a"], k=99) == pytest.approx(0.5)


class TestMrr:
    def test_first_rank(self):
        assert mrr(["a", "b", "c"], ["a"]) == 1.0

    def test_second_rank(self):
        assert mrr(["x", "b"], ["b"]) == pytest.approx(0.5)

    def test_none_found(self):
        assert mrr(["x", "y"], ["a"]) == 0.0

    def test_empty_relevant_set(self):
        assert mrr(["x", "y"], []) == 1.0

    def test_first_relevant_rank_wins(self):
        assert mrr(["x", "b", "a"], ["a", "b"]) == pytest.approx(0.5)
