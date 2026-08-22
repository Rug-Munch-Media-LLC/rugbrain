"""Unit tests for rugbrain.eval.metrics — lightweight RAG-quality metrics.

All metric math is pure stdlib (no models, no network). Verified vectors:
  context_precision(["a","b","c","d"], ["b","d"]) == 0.5  (0.5+0.5)/2
"""

from __future__ import annotations

from typing import Any

import pytest

from rugbrain.eval.metrics import (
    answer_relevance,
    context_precision,
    context_recall,
    faithfulness,
    ragas_available,
    ragas_metric_names,
)


class TestFaithfulness:
    def test_fully_supported(self) -> None:
        answer = "The Euler Finance attack drained 190 million dollars from the protocol."
        context = [
            "The Euler Finance attack drained 190 million dollars from the protocol through a "
            "flash loan exploit."
        ]
        assert faithfulness(answer, context) == pytest.approx(1.0)

    def test_unsupported(self) -> None:
        assert faithfulness("Bitcoin pizza day was epic", ["Euler finance flash loan"]) == 0.0

    def test_partial_two_claims(self) -> None:
        answer = "The attack drained 190 million. Pizza day is awesome."
        context = ["The attack drained 190 million from the protocol."]
        assert faithfulness(answer, context) == pytest.approx(0.5)

    def test_empty_inputs(self) -> None:
        assert faithfulness("", ["context"]) == 0.0
        assert faithfulness("answer", []) == 0.0
        assert faithfulness("", []) == 0.0

    def test_stopwords_do_not_count(self) -> None:
        # All non-stopword tokens appear in context -> supported despite
        # missing function words like "was".
        answer = "The bridge was exploited via signature bypass."
        context = ["bridge exploited via signature bypass, allowing minting"]
        assert faithfulness(answer, context) == pytest.approx(1.0)


class TestAnswerRelevance:
    def test_lexical_identical(self) -> None:
        assert answer_relevance(
            "how does reentrancy work", "how does reentrancy work"
        ) == pytest.approx(1.0)

    def test_lexical_partial(self) -> None:
        # Query tokens: {reentrancy, work} — "work" missing from answer.
        assert answer_relevance("reentrancy guards", "reentrancy work") == pytest.approx(0.5)

    def test_lexical_disjoint(self) -> None:
        assert answer_relevance("euler finance", "pizza toppings") == 0.0

    def test_empty_inputs(self) -> None:
        assert answer_relevance("", "query") == 0.0
        assert answer_relevance("answer", "") == 0.0

    def test_embed_cosine_identical(self) -> Any:
        def embed(text: str) -> list[float]:
            return [1.0, 0.0]

        assert answer_relevance("answer", "query", embed=embed) == pytest.approx(1.0)

    def test_embed_cosine_opposite(self) -> Any:
        def embed(text: str) -> list[float]:
            return [1.0, 0.0] if "q" in text else [-1.0, 0.0]

        assert answer_relevance("other", "q-query", embed=embed) == pytest.approx(0.0)

    def test_embed_cosine_orthogonal(self) -> Any:
        def embed(text: str) -> list[float]:
            return [1.0, 0.0] if "q" in text else [0.0, 1.0]

        assert answer_relevance("other", "q-query", embed=embed) == pytest.approx(0.5)


class TestContextPrecision:
    def test_known_value(self) -> None:
        # Relevant at ranks 2 and 4 -> (1/2 + 2/4)/2 = 0.5.
        assert context_precision(["a", "b", "c", "d"], ["b", "d"]) == pytest.approx(0.5)

    def test_perfect(self) -> None:
        assert context_precision(["a", "b"], ["a", "b"]) == pytest.approx(1.0)

    def test_first_relevant(self) -> None:
        assert context_precision(["a", "x", "y"], ["a"]) == pytest.approx(1.0)

    def test_no_relevant_retrieved(self) -> None:
        assert context_precision(["x", "y", "z"], ["q"]) == 0.0

    def test_empty_inputs(self) -> None:
        assert context_precision([], ["a"]) == 0.0
        assert context_precision(["a"], []) == 0.0

    def test_cutoff(self) -> None:
        # "d" is at rank 4, outside k=3 -> only rank 2 counts: 0.5.
        assert context_precision(["a", "b", "c", "d"], ["b", "d"], k=3) == pytest.approx(0.5)


class TestContextRecall:
    def test_alias_of_recall_at_k(self) -> None:
        assert context_recall(["a", "b", "c"], ["a", "c"]) == pytest.approx(1.0)
        assert context_recall(["a", "x"], ["a", "b"]) == pytest.approx(0.5)

    def test_empty_relevant(self) -> None:
        assert context_recall(["a", "b"], []) == 1.0


class TestRagasOptional:
    def test_available_returns_bool(self) -> None:
        assert isinstance(ragas_available(), bool)

    def test_metric_names_returns_list(self) -> None:
        names = ragas_metric_names()
        assert isinstance(names, list)
        assert len(names) in (0, 3)  # empty without ragas, 3 names with it
