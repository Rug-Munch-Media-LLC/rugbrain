"""Tests for rugbrain.eval.retrieval_eval — retrieval-only eval runner.

Covers golden-set loading, content-based relevance judgment, per-query
scoring, aggregation (incl. latency percentiles), and CLI error paths.
Search functions are fakes — no live endpoint / engine is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.eval.retrieval_eval import (
    GoldenExample,
    _delta_block,
    _item_is_relevant,
    _result_id,
    load_golden_set,
    main,
    percentile,
    run_eval,
    run_query,
    search_via_endpoint,
)

GOLDEN_SET = (
    Path(__file__).resolve().parent.parent / "src" / "rugbrain" / "eval" / "golden_set.jsonl"
)


async def fake_search(query: str, limit: int, collection: str | None) -> list[dict]:
    """Mock search: returns one doc per gold chunk, then distractors."""
    example = _example_for(query)
    results: list[dict] = []
    for i, chunk in enumerate(example.gold_chunks):
        results.append({"id": f"gold-doc-{i}", "content": chunk.lower()})
    while len(results) < limit:
        results.append({"id": f"distractor-{len(results)}", "content": "unrelated text"})
    return results


async def fake_search_miss(query: str, limit: int, collection: str | None) -> list[dict]:
    return [{"id": f"distractor-{i}", "content": "unrelated text"} for i in range(limit)]


async def fake_search_error(query: str, limit: int, collection: str | None) -> list[dict]:
    raise RuntimeError("search backend unavailable")


_EXAMPLES_BY_QUERY: dict[str, GoldenExample] = {}


def _example_for(query: str) -> GoldenExample:
    if not _EXAMPLES_BY_QUERY:
        _EXAMPLES_BY_QUERY.update({ex.query: ex for ex in load_golden_set(GOLDEN_SET)})
    return _EXAMPLES_BY_QUERY[query]


# ── golden-set loading ───────────────────────────────────────────────


class TestLoadGoldenSet:
    def test_parses_golden_set(self):
        examples = load_golden_set(GOLDEN_SET)
        assert len(examples) >= 35
        assert len(examples) <= 50
        assert all(ex.query for ex in examples)
        assert all(ex.has_gold for ex in examples)

    def test_gold_chunks_present(self):
        for ex in load_golden_set(GOLDEN_SET):
            assert ex.gold_chunks, f"query has no gold_chunks: {ex.query!r}"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_golden_set(Path("/nonexistent/golden.jsonl"))

    def test_invalid_line_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text('{"query": "q"}\nnot json\n', encoding="utf-8")
        with pytest.raises(ValueError):
            load_golden_set(bad)


# ── relevance judgment ───────────────────────────────────────────────


class TestRelevance:
    def test_content_substring_match(self):
        ex = GoldenExample(query="q", gold_chunks=["Flash Loan Price Attack"])
        assert _item_is_relevant({"id": "x", "content": "Flash Loan Price Attack. Borrow..."}, ex)
        assert _item_is_relevant({"id": "x", "content": "flash loan price attack in lowercase"}, ex)

    def test_case_insensitive(self):
        ex = GoldenExample(query="q", gold_chunks=["Checks-Effects-Interactions"])
        assert _item_is_relevant(
            {"id": "x", "content": "the checks-effects-interactions pattern"}, ex
        )

    def test_no_match(self):
        ex = GoldenExample(query="q", gold_chunks=["honeypot"])
        assert not _item_is_relevant({"id": "x", "content": "ordinary text"}, ex)

    def test_relevant_doc_ids_still_match(self):
        ex = GoldenExample(query="q", relevant_doc_ids=["abc-123"])
        assert _item_is_relevant({"id": "abc-123", "content": "text"}, ex)
        assert not _item_is_relevant({"id": "other", "content": "text"}, ex)

    def test_empty_gold_is_not_relevant(self):
        ex = GoldenExample(query="q")
        assert not _item_is_relevant({"id": "x", "content": "anything"}, ex)


# ── per-query scoring ────────────────────────────────────────────────


class TestRunQuery:
    async def test_scores_hit_and_recall(self):
        ex = GoldenExample(
            query="flash loan attack how it works", gold_chunks=["Flash Loan Price Attack"]
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

    async def test_ndcg_penalizes_relevant_below_top(self):
        ex = GoldenExample(query="q", gold_chunks=["alpha chunk", "beta chunk"])

        async def late(query: str, limit: int, collection: str | None) -> list[dict]:
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

    async def test_recall_is_fraction_of_gold_chunks(self):
        # Two gold chunks, only one surfaced -> recall@k == 0.5.
        ex = GoldenExample(query="q", gold_chunks=["alpha chunk", "beta chunk"])

        async def partial(query: str, limit: int, collection: str | None) -> list[dict]:
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

    async def test_miss_scores_zero_ndcg_mrr(self):
        ex = GoldenExample(query="query", gold_chunks=["nonexistent-chunk"])
        res = await run_query(fake_search_miss, ex, top_k=5)
        assert res.error == ""
        assert res.hit_at_1 == 0.0
        assert res.hit_at_k == 0.0
        assert res.recall_at_k == 0.0
        assert res.precision_at_k == 0.0
        assert res.ndcg_at_k == 0.0
        assert res.mrr == 0.0

    async def test_error_is_captured(self):
        ex = GoldenExample(query="q", gold_chunks=["a"])
        res = await run_query(fake_search_error, ex, top_k=5)
        assert res.error != ""
        assert res.latency_ms >= 0.0

    async def test_latency_recorded(self):
        ex = GoldenExample(query="q", gold_chunks=["a"])
        res = await run_query(fake_search, ex, top_k=5)
        assert res.latency_ms > 0.0


# ── aggregation + percentiles ────────────────────────────────────────


class TestRunEval:
    async def test_aggregates_over_golden_set(self):
        examples = load_golden_set(GOLDEN_SET)
        report = await run_eval(fake_search, examples, top_k=5)
        assert report.total == len(examples)
        assert report.errors == 0
        assert report.mean_hit_at_1 == pytest.approx(1.0)
        assert report.mean_hit_at_k == pytest.approx(1.0)
        assert report.mean_recall_at_k == pytest.approx(1.0)
        assert report.p50_latency_ms > 0.0
        assert report.p95_latency_ms >= report.p50_latency_ms

    async def test_errors_counted(self):
        examples = [GoldenExample(query="q", gold_chunks=["a"])]
        report = await run_eval(fake_search_error, examples, top_k=5)
        assert report.total == 1
        assert report.errors == 1
        assert report.mean_hit_at_k == 0.0


class TestPercentile:
    def test_nearest_rank(self):
        assert percentile([1, 2, 3, 4], 50) == 2
        assert percentile([1, 2, 3, 4], 95) == 4
        assert percentile([10], 50) == 10

    def test_empty(self):
        assert percentile([], 50) == 0.0


# ── id extraction ────────────────────────────────────────────────────


class TestResultId:
    def test_id_priority(self):
        assert _result_id({"id": "a", "doc_id": "b"}) == "a"
        assert _result_id({"doc_id": "b", "uuid": "c"}) == "b"
        assert _result_id({"uuid": "c"}) == "c"
        assert _result_id({"chunk_id": "d"}) == "d"

    def test_content_fallback(self):
        rid = _result_id({"content": "some text"})
        assert rid.startswith("content:")


# ── endpoint search + rerank wiring ─────────────────────────────────


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, payload: dict):
        self._payload = payload
        self.last_params: dict | None = None
        self.last_url: str | None = None

    async def get(self, url: str, params: dict | None = None, headers: dict | None = None):
        self.last_url = url
        self.last_params = params
        return FakeResponse(self._payload)


class TestSearchViaEndpoint:
    async def test_passes_rerank_param(self):
        client = FakeClient({"results": [{"id": "x", "content": "c"}]})
        out = await search_via_endpoint("http://h", "key", client, "q", 5, "col", rerank=True)
        assert out == [{"id": "x", "content": "c"}]
        assert client.last_params == {"q": "q", "limit": 5, "collection": "col", "rerank": "true"}

    async def test_no_rerank_param_by_default(self):
        client = FakeClient({"results": []})
        await search_via_endpoint("http://h", "key", client, "q", 5, None)
        assert client.last_params == {"q": "q", "limit": 5}

    async def test_captures_reranker_meta(self):
        client = FakeClient(
            {
                "results": [{"id": "x", "content": "c"}],
                "reranker_applied": True,
                "reranker_method": "infinity-bge-reranker-v2-m3",
                "total": 1,
            }
        )
        meta: dict = {}
        await search_via_endpoint("http://h", "key", client, "q", 5, None, rerank=True, meta=meta)
        assert meta["reranker_applied"] is True
        assert meta["reranker_method"] == "infinity-bge-reranker-v2-m3"
        assert meta["total"] == 1

    async def test_meta_absent_when_not_requested(self):
        client = FakeClient({"results": [], "reranker_applied": False})
        await search_via_endpoint("http://h", "key", client, "q", 5, None)
        assert client.last_params.get("rerank") is None


class TestDeltaBlock:
    def test_renders_metric_rows(self):
        from rugbrain.eval.retrieval_eval import EvalReport

        base = EvalReport(mean_hit_at_1=0.80, mean_ndcg_at_k=0.70, mean_mrr=0.85)
        rr = EvalReport(mean_hit_at_1=0.90, mean_ndcg_at_k=0.78, mean_mrr=0.91)
        block = _delta_block(base, rr, k=5)
        assert "NDCG@5" in block
        assert "+0.1000" in block  # hit@1 delta
        assert "baseline" in block
        assert "rerank" in block


# ── CLI ──────────────────────────────────────────────────────────────


class TestCli:
    def test_cli_missing_golden_set(self, capsys: pytest.CaptureFixture[str]):
        code = main(["--golden-set", "/nonexistent.jsonl", "--inprocess"])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_cli_bad_top_k(self, capsys: pytest.CaptureFixture[str]):
        code = main(["--top-k", "0"])
        assert code == 2
        assert "--top-k" in capsys.readouterr().err

    def test_cli_endpoint_requires_api_key(self, capsys: pytest.CaptureFixture[str]):
        code = main(
            [
                "--golden-set",
                str(GOLDEN_SET),
                "--endpoint",
                "http://localhost:9999",
                "--api-key",
                "",
            ]
        )
        assert code == 2
        assert "API key" in capsys.readouterr().err

    async def test_cli_json_report_shape(self):
        examples = load_golden_set(GOLDEN_SET)
        report = await run_eval(fake_search, examples, top_k=5)
        from rugbrain.eval.retrieval_eval import _report_to_dict

        payload = _report_to_dict(report)
        assert payload["k"] == 5
        assert payload["total_queries"] == len(examples)
        assert "p50" in payload["latency_ms"]
        assert "p95" in payload["latency_ms"]
        assert len(payload["per_query"]) == len(examples)
