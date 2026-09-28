"""Tests for rugbrain.eval.harness + rugbrain.eval.gates.

Covers:
* metric math on toy fixtures (recall@k / MRR / nDCG@k via the harness)
* gate pass/fail/skip semantics (shrink-only quality, raise-only latency)
* golden-set schema validation (provenance, required fields, real IDs)
* fail-open behaviour when the retriever raises for every query

No live Qdrant / Redis is touched: retrievers are in-process fakes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rugbrain.eval import verification
from rugbrain.eval.baseline_select import (
    FULL_BASELINES,
    SMOKE_BASELINES,
    select_baseline_name,
    select_baseline_path,
)
from rugbrain.eval.gates import (
    evaluate_gates,
    format_verdict,
    load_baselines,
)
from rugbrain.eval.harness import (
    DEFAULT_GOLDEN,
    BacktestReport,
    GoldenQuery,
    item_is_relevant,
    load_golden,
    percentile,
    result_id,
    run_backtest,
    score_query,
)

GOLDEN = DEFAULT_GOLDEN
# Human verification records live in a sidecar beside the golden set, never
# as a field on the rows themselves. See rugbrain.eval.verification.
EVIDENCE = GOLDEN.parent / verification.EVIDENCE_FILENAME


# ── fixtures ─────────────────────────────────────────────────────────


def _golden(query: str, ids: list[str], collection: str = "c") -> GoldenQuery:
    return GoldenQuery(query=query, collection=collection, relevant_doc_ids=ids)


async def _perfect(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
    """Returns the gold ids first, then distractors (perfect ranking).

    Gold ids are encoded in the query as ``q|<id>,<id>`` so the fake is
    self-contained (no shared mutable state between tests).
    """
    gold = query.split("|", 1)[1].split(",") if "|" in query else []
    out = [{"id": gid, "content": gid} for gid in gold]
    while len(out) < limit:
        out.append({"id": f"distractor-{len(out)}", "content": "unrelated"})
    return out


async def _miss(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
    return [{"id": f"distractor-{i}", "content": "unrelated"} for i in range(limit)]


async def _boom(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
    raise RuntimeError("qdrant down")


# ── metric math on toy fixtures ──────────────────────────────────────


class TestMetricMath:
    async def test_perfect_ranking_scores_one(self) -> None:
        ex = _golden("q|a,b", ["a", "b"])
        score = await score_query(_perfect, ex, top_k=5, k_values=(5,))
        assert score.recall[5] == pytest.approx(1.0)
        assert score.ndcg[5] == pytest.approx(1.0)
        assert score.mrr_value == pytest.approx(1.0)

    async def test_miss_scores_zero(self) -> None:
        ex = _golden("q", ["a", "b"])
        score = await score_query(_miss, ex, top_k=5, k_values=(5,))
        assert score.recall[5] == 0.0
        assert score.ndcg[5] == 0.0
        assert score.mrr_value == 0.0

    async def test_partial_recall_and_mrr(self) -> None:
        # gold "b" at rank 2 -> MRR 0.5, recall 0.5 (1 of 2 found).
        async def retriever(q: str, c: str | None, limit: int) -> list[dict[str, Any]]:
            return [{"id": "x"}, {"id": "b"}, {"id": "y"}]

        ex = _golden("q", ["a", "b"])
        score = await score_query(retriever, ex, top_k=5, k_values=(5,))
        assert score.mrr_value == pytest.approx(0.5)
        assert score.recall[5] == pytest.approx(0.5)

    async def test_cutoff_truncates(self) -> None:
        # gold "b" at rank 3, k=2 -> not seen.
        async def retriever(q: str, c: str | None, limit: int) -> list[dict[str, Any]]:
            return [{"id": "x"}, {"id": "y"}, {"id": "b"}]

        ex = _golden("q", ["b"])
        score = await score_query(retriever, ex, top_k=5, k_values=(2, 5))
        assert score.recall[2] == 0.0
        assert score.recall[5] == pytest.approx(1.0)

    async def test_chunk_mode_content_match(self) -> None:
        async def retriever(q: str, c: str | None, limit: int) -> list[dict[str, Any]]:
            return [{"id": "u1", "content": "the reentrancy guard pattern"}]

        ex = GoldenQuery(query="q", gold_chunks=["reentrancy guard"])
        score = await score_query(retriever, ex, top_k=5, k_values=(5,))
        assert score.recall[5] == pytest.approx(1.0)
        assert score.mrr_value == pytest.approx(1.0)

    async def test_error_captured_not_raised(self) -> None:
        ex = _golden("q", ["a"])
        score = await score_query(_boom, ex, top_k=5, k_values=(5,))
        assert score.error.startswith("RuntimeError")
        assert score.retrieved_ids == []


# ── aggregation + fail-open ──────────────────────────────────────────


class TestRunBacktest:
    async def test_aggregates_means(self) -> None:
        corpus = [_golden("q1|a", ["a"]), _golden("q2|b", ["b"])]
        report = await run_backtest(corpus, _perfect, top_k=5, k_values=(5,))
        assert report.status == "ok"
        assert report.total == 2
        assert report.errors == 0
        assert report.mean_recall[5] == pytest.approx(1.0)
        assert report.mean_mrr == pytest.approx(1.0)

    async def test_all_errors_is_unavailable(self) -> None:
        corpus = [_golden("q1", ["a"]), _golden("q2", ["b"])]
        report = await run_backtest(corpus, _boom, top_k=5, k_values=(5,))
        assert report.status == "unavailable"
        assert report.errors == report.total

    async def test_empty_corpus(self) -> None:
        report = await run_backtest([], _perfect, top_k=5, k_values=(5,))
        assert report.status == "empty"

    async def test_limit_caps_rows(self) -> None:
        corpus = [_golden(f"q{i}", ["a"]) for i in range(20)]
        report = await run_backtest(corpus, _miss, top_k=5, k_values=(5,), limit=3)
        assert report.total == 3

    async def test_latency_percentiles(self) -> None:
        assert percentile([10.0, 20.0, 30.0, 40.0], 50) == 20.0
        assert percentile([10.0, 20.0, 30.0, 40.0], 95) == 40.0
        assert percentile([], 50) == 0.0


# ── gate semantics ───────────────────────────────────────────────────


def _report(recall10: float, mrr: float, ndcg10: float, p95: float = 100.0) -> BacktestReport:
    return BacktestReport(
        total=10,
        errors=0,
        status="ok",
        k_values=(5, 10),
        mean_recall={5: recall10, 10: recall10},
        mean_ndcg={5: ndcg10, 10: ndcg10},
        mean_mrr=mrr,
        p95_latency_ms=p95,
    )


class TestGates:
    def test_pass_when_above_baseline(self) -> None:
        baselines = {"recall@10": {"value": 0.30, "tolerance": 0.05}}
        verdict = evaluate_gates(_report(0.40, 0.5, 0.5), baselines)
        assert verdict.passed
        assert not verdict.skipped

    def test_fail_when_below_tolerance(self) -> None:
        baselines = {"recall@10": {"value": 0.30, "tolerance": 0.05}}
        verdict = evaluate_gates(_report(0.20, 0.5, 0.5), baselines)
        assert not verdict.passed
        assert len(verdict.failures) == 1

    def test_within_tolerance_passes(self) -> None:
        baselines = {"recall@10": {"value": 0.30, "tolerance": 0.05}}
        verdict = evaluate_gates(_report(0.26, 0.5, 0.5), baselines)
        assert verdict.passed

    def test_latency_raise_only(self) -> None:
        baselines = {"p95_latency_ms": {"value": 100.0, "tolerance": 0.50}}
        assert evaluate_gates(_report(0.5, 0.5, 0.5, p95=140.0), baselines).passed
        assert not evaluate_gates(_report(0.5, 0.5, 0.5, p95=200.0), baselines).passed

    def test_unavailable_is_skip_not_fail(self) -> None:
        report = BacktestReport(total=5, errors=5, status="unavailable")
        verdict = evaluate_gates(report, {"recall@10": {"value": 0.3, "tolerance": 0.05}})
        assert verdict.skipped
        assert verdict.passed

    def test_unknown_metric_skipped(self) -> None:
        baselines = {"recall@99": {"value": 0.3, "tolerance": 0.05}}
        verdict = evaluate_gates(_report(0.5, 0.5, 0.5), baselines)
        assert verdict.passed
        assert verdict.results[0].skipped

    def test_mrr_and_ndcg_gated(self) -> None:
        baselines = {
            "mrr": {"value": 0.20, "tolerance": 0.02},
            "ndcg@10": {"value": 0.15, "tolerance": 0.02},
        }
        verdict = evaluate_gates(_report(0.5, 0.10, 0.10), baselines)
        assert not verdict.passed
        assert {r.metric for r in verdict.failures} == {"mrr", "ndcg@10"}

    def test_format_verdict_mentions_fail(self) -> None:
        baselines = {"recall@10": {"value": 0.30, "tolerance": 0.05}}
        text = format_verdict(evaluate_gates(_report(0.10, 0.5, 0.5), baselines))
        assert "FAIL" in text

    def test_load_baselines_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        path.write_text(json.dumps({"mrr": {"value": 0.2, "tolerance": 0.01}}))
        assert load_baselines(path)["mrr"]["value"] == 0.2

    def test_load_baselines_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_baselines(tmp_path / "nope.json")


# ── baseline-set selection ───────────────────────────────────────────


class TestBaselineSelection:
    def test_no_limit_selects_full(self) -> None:
        assert select_baseline_path(None) == FULL_BASELINES
        assert select_baseline_name(None) == "full"

    def test_small_limit_selects_smoke(self) -> None:
        assert select_baseline_path(10, total=41) == SMOKE_BASELINES
        assert select_baseline_name(10, total=41) == "smoke"

    def test_limit_covering_whole_set_selects_full(self) -> None:
        assert select_baseline_path(41, total=41) == FULL_BASELINES
        assert select_baseline_path(100, total=41) == FULL_BASELINES

    def test_limit_without_total_is_smoke(self) -> None:
        assert select_baseline_path(10) == SMOKE_BASELINES

    def test_both_baseline_files_exist_and_parse(self) -> None:
        for path in (SMOKE_BASELINES, FULL_BASELINES):
            assert path.exists(), path
            data = load_baselines(path)
            assert "recall@10" in data
            assert "p95_latency_ms" in data

    def test_gate_uses_smoke_baseline_for_small_limit(self) -> None:
        # A report that clears the smoke baseline but not the full one must
        # pass when gated with limit=10 and fail when gated as a full run.
        report = _report(0.25, 0.10, 0.10)
        assert evaluate_gates(report, limit=10, total=41).passed
        assert not evaluate_gates(report, limit=None).passed

    def test_gate_default_is_full_baseline(self) -> None:
        # No limit => full baseline (recall@10 value 0.63).
        verdict = evaluate_gates(_report(0.20, 0.5, 0.5))
        assert not verdict.passed
        assert any(r.metric == "recall@10" for r in verdict.failures)


# ── golden-set schema validation ─────────────────────────────────────


class TestGoldenSet:
    def test_loads_shipped_golden_set(self) -> None:
        rows = load_golden(GOLDEN)
        assert len(rows) >= 30

    def test_every_row_has_query_and_gold(self) -> None:
        for row in load_golden(GOLDEN):
            assert row.query.strip()
            assert row.has_gold, f"row has no ground truth: {row.query}"

    def test_provenance_values_valid(self) -> None:
        for row in load_golden(GOLDEN):
            assert row.provenance in {"synthetic", "verified"}, row.query

    def test_verified_rows_have_stable_ids(self) -> None:
        # Verification is a HUMAN judgement and is recorded in an evidence
        # sidecar, not asserted here. The original version of this test
        # asserted that at least one row was already verified, which could
        # only ever be satisfied by relabelling the golden set — i.e. by
        # manufacturing the judgement the field exists to record.
        #
        # What is asserted instead is the invariant that makes the claim
        # meaningful: a row may only BE verified when a sufficient evidence
        # record backs it, and such a record necessarily carries the ids that
        # were observed. Editing the golden set cannot make this pass.
        evidence = verification.load_evidence(EVIDENCE)
        for row in load_golden(GOLDEN):
            record = evidence.get(verification.row_id(row.query))
            if verification.decide(record) is verification.Decision.VERIFIED:
                assert record is not None and record.is_sufficient
                assert record.observed_ids, f"verified row lacks ids: {row.query}"

    def test_verified_rows_carry_provenance_note(self) -> None:
        # A verified row must document how it was confirmed against the live
        # corpus. Same evidence-driven framing as above: this checks the
        # records that exist, it does not demand that any exist.
        evidence = verification.load_evidence(EVIDENCE)
        for row in load_golden(GOLDEN):
            record = evidence.get(verification.row_id(row.query))
            if verification.decide(record) is verification.Decision.VERIFIED:
                assert record is not None
                assert record.confirmed_at, f"verified row lacks a date: {row.query}"
                assert record.reviewer, f"verified row lacks a reviewer: {row.query}"

    def test_all_rows_verified(self) -> None:
        # Renamed in substance: the original asserted every row was verified
        # against "the 2026-09-19 hardening pass", a pass that never happened —
        # the shipped rows carried no provenance at all, so `verified` was the
        # empty set. The honest invariant is that every row is ACCOUNTED FOR
        # (falls into exactly one decision) and that the number still needing a
        # human is reported rather than hidden.
        rows = load_golden(GOLDEN)
        queries = [r.query for r in rows]
        evidence = verification.load_evidence(EVIDENCE)
        tally = verification.counts(queries, evidence)
        accounted = tally["verified"] + tally["unverified"] + tally["pending"] + tally["rejected"]
        assert accounted == tally["total"] == len(rows)
        # A verified row always carries gold ids, so it is actually scorable.
        for row in rows:
            record = evidence.get(verification.row_id(row.query))
            if verification.decide(record) is verification.Decision.VERIFIED:
                assert row.relevant_doc_ids or row.gold_chunks, row.query

    def test_categories_are_known(self) -> None:
        known = {"coding", "scam", "incident", "market", "research", "general"}
        for row in load_golden(GOLDEN):
            assert row.category in known, row.query

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_golden(tmp_path / "nope.jsonl")

    def test_invalid_line_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"query": "ok", "relevant_doc_ids": ["a"]}\nnot json\n')
        with pytest.raises(ValueError):
            load_golden(path)


# ── result helpers ───────────────────────────────────────────────────


class TestResultHelpers:
    def test_result_id_priority(self) -> None:
        assert result_id({"id": "a", "doc_id": "b"}) == "a"
        assert result_id({"doc_id": "b"}) == "b"
        assert result_id({"chunk_id": "c"}) == "c"

    def test_result_id_content_fallback(self) -> None:
        assert result_id({"content": "hello"}).startswith("content:")

    def test_item_is_relevant_id_match(self) -> None:
        ex = _golden("q", ["a"])
        assert item_is_relevant({"id": "a"}, ex)
        assert not item_is_relevant({"id": "z"}, ex)

    def test_item_is_relevant_chunk_match(self) -> None:
        ex = GoldenQuery(query="q", gold_chunks=["Reentrancy"])
        assert item_is_relevant({"id": "u", "content": "the reentrancy guard"}, ex)
        assert not item_is_relevant({"id": "u", "content": "unrelated"}, ex)
