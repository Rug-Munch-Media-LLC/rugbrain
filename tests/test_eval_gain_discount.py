"""Unit tests for rugbrain.eval.gain_discount — the shared NDCG arithmetic.

Split out of `eval/ndcg.py` on 2026-09-29 (size ratchet). These primitives are
the single place the position discount and the ideal-DCG shape are defined, so
they are pinned here rather than only implicitly through the metric tests.
"""

from __future__ import annotations

import math

import pytest

from rugbrain.eval.gain_discount import clamp_k, dcg_at_k, idcg_binary


class TestClampK:
    def test_none_means_everything_available(self) -> None:
        assert clamp_k(None, 5) == 5

    def test_explicit_k_passes_through(self) -> None:
        assert clamp_k(2, 5) == 2

    def test_k_larger_than_list_is_clamped(self) -> None:
        # A cutoff beyond the result list means "everything", not an error.
        assert clamp_k(99, 3) == 3

    def test_negative_k_is_preserved_for_the_caller_to_reject(self) -> None:
        # clamp_k does not floor at zero — callers test k <= 0 themselves so
        # "no window" and "not provided" stay distinguishable.
        assert clamp_k(-1, 5) == -1


class TestDcgAtK:
    def test_empty_gains_is_zero(self) -> None:
        assert dcg_at_k([], 3) == 0.0

    def test_single_gain_at_rank_one(self) -> None:
        # log2(1 + 2) = log2(2) = 1, so rank 1 is undiscounted.
        assert dcg_at_k([1.0], 1) == pytest.approx(1.0)

    def test_graded_gains_accumulate_with_discount(self) -> None:
        got = dcg_at_k([1.0, 0.5], 2)
        want = 1.0 / math.log2(2) + 0.5 / math.log2(3)
        assert got == pytest.approx(want)

    def test_k_truncates_the_window(self) -> None:
        assert dcg_at_k([1.0, 1.0, 1.0], 2) == pytest.approx(dcg_at_k([1.0, 1.0], 2))

    def test_k_zero_is_zero(self) -> None:
        assert dcg_at_k([1.0, 1.0], 0) == 0.0


class TestIdcgBinary:
    def test_zero_relevant_is_zero(self) -> None:
        assert idcg_binary(0, 3) == 0.0

    def test_matches_hand_computed_ideal(self) -> None:
        # 2 relevant in a k=4 window: ranks 1 and 2 carry the gain.
        got = idcg_binary(2, 4)
        want = 1.0 / math.log2(2) + 1.0 / math.log2(3)
        assert got == pytest.approx(want)

    def test_capped_at_the_cutoff(self) -> None:
        # 5 relevant but k=2 -> only two slots can ever be filled.
        assert idcg_binary(5, 2) == pytest.approx(idcg_binary(2, 2))

    def test_ideal_never_below_actual_for_perfect_ranking(self) -> None:
        actual = dcg_at_k([1.0, 1.0], 2)
        assert actual <= idcg_binary(2, 2) + 1e-12
