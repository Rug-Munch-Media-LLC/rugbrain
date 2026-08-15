"""Tests for rugbrain.eval.run_eval — labeled eval CLI + runner.

Covers the CLI dry-run against the shipped sample dataset (with a mocked
search function), JSON output, error handling, and result-id extraction.

``fake_search`` is module-level so the CLI's dotted-path loader
(``load_search_function("tests.test_eval_cli.fake_search")``) can import it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.eval.run_eval import (
    QueryExample,
    _result_id,
    load_dataset,
    load_search_function,
    main,
    run_eval,
    run_query,
)

SAMPLE_DATASET = (
    Path(__file__).resolve().parent.parent / "src" / "rugbrain" / "eval" / "sample_dataset.jsonl"
)

# Mock ground truth derived from the shipped dataset so the fake search always
# returns the labeled relevant docs for every query (perfect retrieval).
_RELEVANT_BY_QUERY: dict[str, list[str]] = {
    ex.query: ex.relevant_doc_ids for ex in load_dataset(SAMPLE_DATASET)
}


async def fake_search(query: str, limit: int) -> list[dict]:
    """Perfect mocked search: returns the relevant ids first, then distractors."""
    relevant = _RELEVANT_BY_QUERY.get(query, ["relevant-a", "relevant-b"])
    results: list[dict] = [{"id": rid, "content": f"content {rid}"} for rid in relevant[:limit]]
    while len(results) < limit:
        results.append({"id": f"distractor-{len(results)}", "content": "distractor content"})
    return results


async def fake_search_with_doc_id_key(query: str, limit: int) -> list[dict]:
    """Search returning ``doc_id`` instead of ``id`` (extraction fallback)."""
    return [{"doc_id": f"doc-{i}", "content": f"content {i}"} for i in range(limit)]


async def fake_search_error(query: str, limit: int) -> list[dict]:
    raise RuntimeError("search backend unavailable")


async def fake_search_missing_id(query: str, limit: int) -> list[dict]:
    return [{"content": "no id here"}, {"content": "still none"}]


# ── runner ───────────────────────────────────────────────────────────


class TestRunQuery:
    async def test_scores_perfect_ranking(self):
        ex = QueryExample(
            query="How was the Euler Finance flash loan attack executed?",
            relevant_doc_ids=["defi_exploits/euler-finance-2023", "flash_loan_attack_pattern"],
        )
        res = await run_query(fake_search, ex, top_k=5)
        assert res.error == ""
        assert res.retrieved_ids[:2] == ex.relevant_doc_ids
        assert res.ndcg == pytest.approx(1.0)
        assert res.recall == pytest.approx(1.0)
        assert res.hit_rate == 1.0
        assert res.mrr_value == 1.0

    async def test_expected_answer_triggers_content_metrics(self):
        ex = QueryExample(
            query="How was the Euler Finance flash loan attack executed?",
            relevant_doc_ids=["defi_exploits/euler-finance-2023"],
            expected_answer="The Euler attack drained 190 million dollars through a flash loan.",
        )
        res = await run_query(fake_search, ex, top_k=5)
        assert res.faithfulness_score is not None
        assert res.answer_relevance_score is not None
        assert 0.0 <= res.faithfulness_score <= 1.0

    async def test_search_error_is_captured(self):
        ex = QueryExample(query="q", relevant_doc_ids=["a"])
        res = await run_query(fake_search_error, ex, top_k=5)
        assert res.error != ""
        assert res.ndcg == 0.0

    async def test_missing_id_is_error_not_crash(self):
        ex = QueryExample(query="q", relevant_doc_ids=["a"])
        res = await run_query(fake_search_missing_id, ex, top_k=5)
        assert "no id key" in res.error

    async def test_extracts_doc_id_key_fallback(self):
        ex = QueryExample(query="q", relevant_doc_ids=["doc-0"])
        res = await run_query(fake_search_with_doc_id_key, ex, top_k=5)
        assert res.retrieved_ids == ["doc-0", "doc-1", "doc-2", "doc-3", "doc-4"]


class TestRunEval:
    async def test_aggregates_metrics(self):
        examples = load_dataset(SAMPLE_DATASET)
        summary = await run_eval(fake_search, examples, top_k=5)
        assert summary.total == 10
        assert summary.errors == 0
        assert summary.mean_ndcg == pytest.approx(1.0)
        assert summary.mean_recall == pytest.approx(1.0)
        assert summary.mean_hit_rate == 1.0
        assert len(summary.per_query) == 10

    async def test_errors_are_counted(self):
        examples = [QueryExample(query="q", relevant_doc_ids=["a"])]
        summary = await run_eval(fake_search_error, examples, top_k=5)
        assert summary.total == 1
        assert summary.errors == 1
        assert summary.mean_ndcg == 0.0


# ── dataset + id extraction ──────────────────────────────────────────


class TestLoadDataset:
    def test_parses_sample(self):
        examples = load_dataset(SAMPLE_DATASET)
        assert len(examples) == 10
        assert all(ex.query for ex in examples)
        assert all(ex.relevant_doc_ids for ex in examples)

    def test_missing_dataset_raises(self):
        with pytest.raises(FileNotFoundError):
            load_dataset(Path("/nonexistent/dataset.jsonl"))

    def test_invalid_line_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text('{"query": "q"}\nnot json\n', encoding="utf-8")
        with pytest.raises(ValueError):
            load_dataset(bad)

    def test_ignores_blank_and_comment_lines(self, tmp_path: Path):
        f = tmp_path / "mixed.jsonl"
        f.write_text('# comment\n\n{"query": "q", "relevant_doc_ids": ["a"]}\n', encoding="utf-8")
        assert len(load_dataset(f)) == 1


class TestResultId:
    def test_id_priority(self):
        assert _result_id({"id": "a", "doc_id": "b"}) == "a"
        assert _result_id({"doc_id": "b", "uuid": "c"}) == "b"
        assert _result_id({"uuid": "c"}) == "c"
        assert _result_id({"chunk_id": "d"}) == "d"

    def test_missing_key_raises(self):
        with pytest.raises(ValueError):
            _result_id({"content": "x"})


# ── CLI ──────────────────────────────────────────────────────────────


class TestCli:
    def test_load_search_function_dotted_path(self):
        fn = load_search_function("tests.test_eval_cli.fake_search")
        assert callable(fn)

    def test_invalid_dotted_path(self):
        with pytest.raises(ValueError):
            load_search_function("no-dotted-path")

    def test_cli_dry_run(self, capsys: pytest.CaptureFixture[str]):
        code = main(
            [
                "--dataset",
                str(SAMPLE_DATASET),
                "--top-k",
                "5",
                "--search",
                "tests.test_eval_cli.fake_search",
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "rugbrain eval" in out
        assert "mean ndcg@5=1.0000" in out
        assert "recall@5=1.0000" in out
        assert "errors=0" in out

    def test_cli_json_output(self, capsys: pytest.CaptureFixture[str]):
        code = main(
            [
                "--dataset",
                str(SAMPLE_DATASET),
                "--top-k",
                "3",
                "--search",
                "tests.test_eval_cli.fake_search",
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["total_queries"] == 10
        assert payload["mean_ndcg"] == pytest.approx(1.0)
        assert len(payload["per_query"]) == 10

    def test_cli_missing_dataset(self, capsys: pytest.CaptureFixture[str]):
        code = main(
            ["--dataset", "/nonexistent.jsonl", "--search", "tests.test_eval_cli.fake_search"]
        )
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_cli_bad_top_k(self, capsys: pytest.CaptureFixture[str]):
        code = main(["--top-k", "0"])
        assert code == 2
        assert "--top-k" in capsys.readouterr().err

    def test_cli_bad_search_path(self, capsys: pytest.CaptureFixture[str]):
        code = main(["--dataset", str(SAMPLE_DATASET), "--search", "no.such.fn"])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_cli_all_errors_exit_nonzero(self, capsys: pytest.CaptureFixture[str]):
        code = main(
            [
                "--dataset",
                str(SAMPLE_DATASET),
                "--search",
                "tests.test_eval_cli.fake_search_error",
            ]
        )
        out = capsys.readouterr().out
        assert code == 1
        assert "errors=10" in out
