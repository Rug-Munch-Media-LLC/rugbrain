"""CLI + endpoint tests (split from test_eval_retrieval 2026-10-15)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rugbrain.eval.retrieval_eval import (
    GoldenExample,
    _delta_block,
    load_golden_set,
    main,
    run_eval,
    search_via_endpoint,
)

GOLDEN_SET = (
    Path(__file__).resolve().parent.parent / "src" / "rugbrain" / "eval" / "golden_set.jsonl"
)


async def fake_search(query: str, limit: int, collection: str | None) -> Any:
    """Mock search: one doc per gold chunk (self-contained copy for this file)."""
    _load_examples()
    example = _EXAMPLES_BY_QUERY.get(query)
    chunks = example.gold_chunks if example is not None else [query]
    results: list[dict[str, Any]] = []
    for i, chunk in enumerate(chunks):
        results.append({"id": f"gold-doc-{i}", "content": chunk.lower()})
    while len(results) < limit:
        results.append({"id": f"distractor-{len(results)}", "content": "unrelated text"})
    return results


_EXAMPLES_BY_QUERY: dict[str, GoldenExample] = {}


def _load_examples() -> None:
    """Populate the query -> example map from the shipped golden set (once)."""
    if not _EXAMPLES_BY_QUERY:
        _EXAMPLES_BY_QUERY.update({ex.query: ex for ex in load_golden_set(GOLDEN_SET)})


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.last_params: dict[str, Any] | None = {}
        self.last_url: str | None = None

    async def get(
            self,
            url: str,
            params: dict[str, Any] | None = None,
            headers: dict[str, Any] | None = None,
        ) -> Any:
        self.last_url = url
        self.last_params = params
        return FakeResponse(self._payload)


class TestSearchViaEndpoint:
    async def test_passes_rerank_param(self) -> None:
        client = FakeClient({"results": [{"id": "x", "content": "c"}]})
        out = await search_via_endpoint("http://h", "key", client, "q", 5, "col", rerank=True)
        assert out == [{"id": "x", "content": "c"}]
        assert client.last_params == {"q": "q", "limit": 5, "collection": "col", "rerank": "true"}

    async def test_no_rerank_param_by_default(self) -> None:
        client = FakeClient({"results": []})
        await search_via_endpoint("http://h", "key", client, "q", 5, None)
        assert client.last_params == {"q": "q", "limit": 5}

    async def test_captures_reranker_meta(self) -> None:
        client = FakeClient(
            {
                "results": [{"id": "x", "content": "c"}],
                "reranker_applied": True,
                "reranker_method": "qwen3-reranker-8b",
                "total": 1,
            }
        )
        meta: dict[str, Any] = {}
        await search_via_endpoint("http://h", "key", client, "q", 5, None, rerank=True, meta=meta)
        assert meta["reranker_applied"] is True
        assert meta["reranker_method"] == "qwen3-reranker-8b"
        assert meta["total"] == 1

    async def test_meta_absent_when_not_requested(self) -> None:
        client = FakeClient({"results": [], "reranker_applied": False})
        await search_via_endpoint("http://h", "key", client, "q", 5, None)
        assert (client.last_params or {}).get("rerank") is None


class TestDeltaBlock:
    def test_renders_metric_rows(self) -> None:
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
    def test_cli_missing_golden_set(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--golden-set", "/nonexistent.jsonl", "--inprocess"])
        assert code == 2
        assert "error:" in capsys.readouterr().err

    def test_cli_bad_top_k(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--top-k", "0"])
        assert code == 2
        assert "--top-k" in capsys.readouterr().err

    def test_cli_endpoint_requires_api_key(self, capsys: pytest.CaptureFixture[str]) -> None:
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

    async def test_cli_json_report_shape(self) -> None:
        examples = load_golden_set(GOLDEN_SET)
        report = await run_eval(fake_search, examples, top_k=5)
        from rugbrain.eval.retrieval_eval import _report_to_dict

        payload = _report_to_dict(report)
        assert payload["k"] == 5
        assert payload["total_queries"] == len(examples)
        assert "p50" in payload["latency_ms"]
        assert "p95" in payload["latency_ms"]
        assert len(payload["per_query"]) == len(examples)
