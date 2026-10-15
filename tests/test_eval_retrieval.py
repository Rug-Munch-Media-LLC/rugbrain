"""Tests for rugbrain.eval.retrieval_eval — retrieval-only eval runner.

Covers golden-set loading, content-based relevance judgment, per-query
scoring, aggregation (incl. latency percentiles), and CLI error paths.
Search functions are fakes — no live endpoint / engine is touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rugbrain.eval.retrieval_eval import (
    GoldenExample,
    _item_is_relevant,
    _result_id,
    load_golden_set,
    percentile,
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


# ── golden-set loading ───────────────────────────────────────────────


class TestLoadGoldenSet:
    def test_parses_golden_set(self) -> None:
        examples = load_golden_set(GOLDEN_SET)
        assert len(examples) >= 35
        assert len(examples) <= 50
        assert all(ex.query for ex in examples)
        assert all(ex.has_gold for ex in examples)

    def test_gold_chunks_present(self) -> None:
        for ex in load_golden_set(GOLDEN_SET):
            assert ex.gold_chunks, f"query has no gold_chunks: {ex.query!r}"

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_golden_set(Path("/nonexistent/golden.jsonl"))

    def test_invalid_line_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.jsonl"
        bad.write_text('{"query": "q"}\nnot json\n', encoding="utf-8")
        with pytest.raises(ValueError):
            load_golden_set(bad)


# ── relevance judgment ───────────────────────────────────────────────


class TestRelevance:
    def test_content_substring_match(self) -> None:
        ex = GoldenExample(query="q", gold_chunks=["Flash Loan Price Attack"])
        assert _item_is_relevant({"id": "x", "content": "Flash Loan Price Attack. Borrow..."}, ex)
        assert _item_is_relevant({"id": "x", "content": "flash loan price attack in lowercase"}, ex)

    def test_case_insensitive(self) -> None:
        ex = GoldenExample(query="q", gold_chunks=["Checks-Effects-Interactions"])
        assert _item_is_relevant(
            {"id": "x", "content": "the checks-effects-interactions pattern"}, ex
        )

    def test_no_match(self) -> None:
        ex = GoldenExample(query="q", gold_chunks=["honeypot"])
        assert not _item_is_relevant({"id": "x", "content": "ordinary text"}, ex)

    def test_relevant_doc_ids_still_match(self) -> None:
        ex = GoldenExample(query="q", relevant_doc_ids=["abc-123"])
        assert _item_is_relevant({"id": "abc-123", "content": "text"}, ex)
        assert not _item_is_relevant({"id": "other", "content": "text"}, ex)

    def test_empty_gold_is_not_relevant(self) -> None:
        ex = GoldenExample(query="q")
        assert not _item_is_relevant({"id": "x", "content": "anything"}, ex)


class TestPercentile:
    def test_nearest_rank(self) -> None:
        assert percentile([1, 2, 3, 4], 50) == 2
        assert percentile([1, 2, 3, 4], 95) == 4
        assert percentile([10], 50) == 10

    def test_empty(self) -> None:
        assert percentile([], 50) == 0.0


# ── id extraction ────────────────────────────────────────────────────


class TestResultId:
    def test_id_priority(self) -> None:
        assert _result_id({"id": "a", "doc_id": "b"}) == "a"
        assert _result_id({"doc_id": "b", "uuid": "c"}) == "b"
        assert _result_id({"uuid": "c"}) == "c"
        assert _result_id({"chunk_id": "d"}) == "d"

    def test_content_fallback(self) -> None:
        rid = _result_id({"content": "some text"})
        assert rid.startswith("content:")


# ── endpoint search + rerank wiring ─────────────────────────────────


