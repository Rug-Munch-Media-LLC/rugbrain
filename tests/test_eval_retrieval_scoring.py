"""Per-query + per-eval scoring tests (split from test_eval_retrieval 2026-10-15)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rugbrain.eval.retrieval_eval import (
    GoldenExample,
    load_golden_set,
    run_eval,
    run_query,
)

GOLDEN_SET = (
    Path(__file__).resolve().parent.parent / "src" / "rugbrain" / "eval" / "golden_set.jsonl"
)


async def fake_search(query: str, limit: int, collection: str | None) -> Any:
    """Mock search: returns one doc per gold chunk, then distractors.

    Chunks come from the golden file's row for ``query`` when present; for a
    query that is not in the file the query string itself is echoed as the
    chunk, so a test can assert a hit without coupling to golden-file content.
    """
    _load_examples()
    example = _EXAMPLES_BY_QUERY.get(query)
    chunks = example.gold_chunks if example is not None else [query]
    results: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        results.append({"id": f"gold-doc-{i}", "content": chunk.lower()})
    while len(results) < limit:
        results.append({"id": f"distractor-{len(results)}", "content": "unrelated text"})
    return results


async def fake_search_miss(query: str, limit: int, collection: str | None) -> Any:
    return [{"id": f"distractor-{i}", "content": "unrelated text"} for i in range(limit)]


async def fake_search_error(query: str, limit: int, collection: str | None) -> Any:
    raise RuntimeError("search backend unavailable")


_EXAMPLES_BY_QUERY: dict[str, GoldenExample] = {}


def _load_examples() -> None:
    """Populate the query -> example map from the shipped golden set (once)."""
    if not _EXAMPLES_BY_QUERY:
        _EXAMPLES_BY_QUERY.update({ex.query: ex for ex in load_golden_set(GOLDEN_SET)})




# ── per-query scoring ────────────────────────────────────────────────


class TestRunQuery:
    async def test_scores_hit_and_recall(self) -> None:
        ex = GoldenExample(
            query="a query not in the golden file", gold_chunks=["a query not in the golden file"]
        )
        res = await run_query(fake_search, ex, top_k=5)
        assert res.error == ""
        assert res.hit_at_1 == 1.0
        assert res.hit_at_k == 1.0
        assert res.recall_at_k == 1.0
        assert res.precision_at_k == pytest.approx(1 / 5)
        # All gold docs at the top -> perfect binary-graded ranking.
        assert res.ndcg_at_k == pytest.approx(1.0)
        assert res.mrr == pytest.approx(1.0)

    async def test_ndcg_penalizes_relevant_below_top(self) -> Any:
        ex = GoldenExample(query="q", gold_chunks=["alpha chunk", "beta chunk"])

        async def late(query: str, limit: int, collection: str | None) -> list[dict[str, Any]]:
            return [
                {"id": "d1", "content": "noise"},
                {"id": "d2", "content": "alpha chunk content"},
                {"id": "d3", "content": "noise"},
                {"id": "d4", "content": "beta chunk content"},
                {"id": "d5", "content": "noise"},
            ]

        res = await run_query(late, ex, top_k=5)
        assert res.hit_at_k == 1.0
        assert res.recall_at_k == pytest.approx(1.0)
        assert res.mrr == pytest.approx(0.5)  # first relevant at rank 2
        assert res.ndcg_at_k < 1.0  # relevant docs buried below noise
        assert res.ndcg_at_k > 0.0

    async def test_recall_is_fraction_of_gold_chunks(self) -> Any:
        # Two gold chunks, only one surfaced -> recall@k == 0.5.
        ex = GoldenExample(query="q", gold_chunks=["alpha chunk", "beta chunk"])

        async def partial(query: str, limit: int, collection: str | None) -> list[dict[str, Any]]:
            return [
                {"id": "doc", "content": "alpha chunk content"},
                {"id": "d2", "content": "noise"},
            ]

        res = await run_query(partial, ex, top_k=5)
        assert res.hit_at_k == 1.0
        assert res.recall_at_k == pytest.approx(0.5)
        assert res.precision_at_k == pytest.approx(1 / 5)
        # The only relevant doc is at rank 1 -> perfect ranking of what was found.
        assert res.ndcg_at_k == pytest.approx(1.0)
        assert res.mrr == pytest.approx(1.0)

    async def test_miss_scores_zero_ndcg_mrr(self) -> None:
        ex = GoldenExample(query="query", gold_chunks=["nonexistent-chunk"])
        res = await run_query(fake_search_miss, ex, top_k=5)
        assert res.error == ""
        assert res.hit_at_1 == 0.0
        assert res.hit_at_k == 0.0
        assert res.recall_at_k == 0.0
        assert res.precision_at_k == 0.0
        assert res.ndcg_at_k == 0.0
        assert res.mrr == 0.0

    async def test_error_is_captured(self) -> None:
        ex = GoldenExample(query="q", gold_chunks=["a"])
        res = await run_query(fake_search_error, ex, top_k=5)
        assert res.error != ""
        assert res.latency_ms >= 0.0

    async def test_latency_recorded(self) -> None:
        ex = GoldenExample(query="q", gold_chunks=["a"])
        res = await run_query(fake_search, ex, top_k=5)
        assert res.latency_ms > 0.0


# ── aggregation + percentiles ────────────────────────────────────────


class TestRunEval:
    async def test_aggregates_over_golden_set(self) -> None:
        examples = load_golden_set(GOLDEN_SET)
        report = await run_eval(fake_search, examples, top_k=5)
        assert report.total == len(examples)
        assert report.errors == 0
        assert report.mean_hit_at_1 == pytest.approx(1.0)
        assert report.mean_hit_at_k == pytest.approx(1.0)
        assert report.mean_recall_at_k == pytest.approx(1.0)
        assert report.p50_latency_ms > 0.0
        assert report.p95_latency_ms >= report.p50_latency_ms

    async def test_errors_counted(self) -> None:
        examples = [GoldenExample(query="q", gold_chunks=["a"])]
        report = await run_eval(fake_search_error, examples, top_k=5)
        assert report.total == 1
        assert report.errors == 1
        assert report.mean_hit_at_k == 0.0


