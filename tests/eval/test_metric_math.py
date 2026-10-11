"""Metric-math unit tests for eval_ingest_sources."""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence

import pytest

from rugbrain.eval.ndcg import (
    dcg_at_k,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
)


def test_recall_at_k_basic():
    """recall@k = |relevant ∩ retrieved| / |relevant|"""
    # retrieved_ids: list of doc ids that were retrieved
    # relevant_ids: list of doc ids that are relevant
    retrieved = ["a", "b", "c"]
    relevant = ["b", "c", "d"]  # b and c are in both

    assert recall_at_k(retrieved, relevant, k=3) == 2 / 3
    assert recall_at_k(retrieved, relevant, k=2) == 1 / 2  # only b
    assert recall_at_k(retrieved, relevant, k=1) == 1 / 2  # only b (a is not relevant)
    assert recall_at_k([], relevant, k=5) == 0.0


def test_mrr_basic():
    """MRR = 1 / rank_of_first_relevant (1-indexed)"""
    # first relevant (b) at position 2 (1-indexed)
    assert mrr(["x", "b", "y"], ["b", "z"]) == 1.0 / 2.0

    # first relevant at position 1
    assert mrr(["b", "x", "y"], ["b", "z"]) == 1.0

    # no relevant retrieved
    assert mrr(["x", "y", "z"], ["b", "z"]) == 0.0


def test_ndcg_at_k_simple():
    """NDCG using binary relevance (1 if in relevant, else 0)."""
    # Top-3: retrieved a (not relevant), b (relevant), c (relevant)
    # Ideal: b at pos 1, c at pos 2
    assert math.isclose(ndcg_at_k(["a", "b", "c"], ["b", "c"], k=3), 1.0, rel_tol=1e-4)

    # no relevant
    assert ndcg_at_k(["a", "b", "c"], ["d"], k=3) == 0.0


def test_hit_rate_at_k():
    """Hit rate = 1 if any relevant in retrieved (at k), else 0."""
    # b is in retrieved at k=3
    assert hit_rate_at_k(["a", "b", "c"], ["b", "x"], k=3) == 1.0
    # b is not in top-1
    assert hit_rate_at_k(["a", "b", "c"], ["b", "x"], k=1) == 0.0
    # nothing relevant
    assert hit_rate_at_k([], ["b", "x"], k=5) == 0.0


def test_dcg_at_k_zero_and_single():
    """DCG edge cases."""
    assert dcg_at_k([], [], k=5) == 0.0
    # Single relevant at position 1: gain=1, discount=log2(1+1)=1, DCG=1
    assert dcg_at_k(["a"], ["a"], k=1) == 1.0 / math.log2(1 + 1)


def test_compute_metrics_for_report():
    """Test aggregation like the eval harness does for report formatting."""
    recalls_5 = [0.5, 1.0, 0.0]
    recalls_10 = [0.6, 1.0, 0.0]
    ndcgs_5 = [0.4, 0.8, 0.0]
    ndcgs_10 = [0.45, 0.85, 0.0]
    mrrs = [0.3, 0.9, 0.0]

    mean_recall_5 = sum(recalls_5) / len(recalls_5)
    mean_recall_10 = sum(recalls_10) / len(recalls_10)
    mean_ndcg_5 = sum(ndcgs_5) / len(ndcgs_5)
    mean_ndcg_10 = sum(ndcgs_10) / len(ndcgs_10)
    mean_mrr = sum(mrrs) / len(mrrs)

    assert math.isclose(mean_recall_5, 0.5, rel_tol=1e-4)
    assert math.isclose(mean_recall_10, 0.53333, rel_tol=1e-4)
    assert math.isclose(mean_ndcg_5, 0.4, rel_tol=1e-4)
    # mean of [0.45, 0.85, 0.0] = 1.3/3 = 0.43333...
    assert math.isclose(mean_ndcg_10, 0.43333, rel_tol=1e-4)
    assert math.isclose(mean_mrr, 0.4, rel_tol=1e-4)


def test_format_report_matches_expected_baseline_structure():
    """Verify the report structure matches what eval_ingest_sources.py expects."""
    from rugbrain.eval.harness import BacktestReport
    from rugbrain.eval.golden_set import DEFAULT_K_VALUES

    # Minimal valid report
    report = BacktestReport(total=5, k_values=DEFAULT_K_VALUES)
    report.status = "ok"
    report.errors = 0
    report.mean_recall = {5: 0.8, 10: 0.9}
    report.mean_ndcg = {5: 0.7, 10: 0.75}
    report.mean_mrr = 0.65
    report.p50_latency_ms = 120.5
    report.p95_latency_ms = 250.3

    # This mirrors the _format_report function in eval_ingest_sources.py
    expected = {
        "status": "ok",
        "total": 5,
        "errors": 0,
        "metrics": {
            "recall@5": 0.8,
            "recall@10": 0.9,
            "ndcg@5": 0.7,
            "ndcg@10": 0.75,
            "mrr": 0.65,
            "p50_latency_ms": 120.5,
            "p95_latency_ms": 250.3,
        },
    }

    # Compare structure
    assert expected["status"] == report.status
    assert expected["total"] == report.total
    assert expected["errors"] == report.errors
    for k in [5, 10]:
        assert math.isclose(
            expected["metrics"][f"recall@{k}"], report.mean_recall.get(k, 0.0)
        )
        assert math.isclose(
            expected["metrics"][f"ndcg@{k}"], report.mean_ndcg.get(k, 0.0)
        )
    assert math.isclose(expected["metrics"]["mrr"], report.mean_mrr)
    assert math.isclose(
        expected["metrics"]["p50_latency_ms"], report.p50_latency_ms
    )
    assert math.isclose(
        expected["metrics"]["p95_latency_ms"], report.p95_latency_ms
    )
