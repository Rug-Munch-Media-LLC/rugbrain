"""The offline backtest RUNNER — score a golden set through an injected retriever.

This is the CI-callable sibling of ``retrieval_eval`` (live endpoint) and
``run_eval`` (labeled doc-id CLI). It is deliberately *offline-first*: the
retriever is injected, so the same harness runs against a recorded fixture in
PR CI and against live Qdrant in the nightly job. No LLM-judge dependency is
required — Ragas/DeepEval are optional extras (``requirements-eval.txt``) and
the harness degrades gracefully when they are absent.

Contract::

    run_backtest(corpus, retriever, top_k=10, k_values=(5, 10)) -> BacktestReport

Fail-open: when the retriever raises for every query the report is returned
with ``status="unavailable"`` and ``errors == total`` — the caller (gates /
smoke script) decides whether that is a skip or a failure. The harness never
crashes on a missing backend. Note that fail-open is a HARNESS property, not a
GATE property: ``missioneval.gate`` deliberately inverts it, because a broken
run must never be readable as a pass.

Responsibility split:

    golden_set       result matching, percentiles, JSONL loading
    backtest_report  the report shape and its JSON projection
    qdrant_retriever the direct-Qdrant retriever factory
    harness (this)   the runner: score each query, then aggregate
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from rugbrain.eval.backtest_report import (
    BacktestReport,
    QueryScore,
    report_to_json,
    run_backtest_sync,
)
from rugbrain.eval.golden_set import (
    DEFAULT_GOLDEN,
    DEFAULT_K_VALUES,
    GoldenQuery,
    item_is_relevant,
    load_golden,
    percentile,
    result_id,
)

# The four eval scripts (``run-eval-smoke.sh``, ``run-eval-nightly.sh``,
# ``eval_rerank_ab.py``, ``run-mission-eval.py``) import the direct-Qdrant
# retriever from here. It is re-exported, not re-implemented: the concern lives
# in qdrant_retriever, the import contract lives in harness.
from rugbrain.eval.qdrant_retriever import direct_qdrant_retriever
from rugbrain.eval.query_scoring import score_query

logger = logging.getLogger(__name__)


async def run_backtest(
    corpus: Path | str | Sequence[GoldenQuery] = DEFAULT_GOLDEN,
    retriever: Callable[..., Any] | None = None,
    top_k: int = 10,
    k_values: Iterable[int] = DEFAULT_K_VALUES,
    limit: int | None = None,
) -> BacktestReport:
    """
    Run a golden set through *retriever* and aggregate retrieval metrics.

    Args:
        corpus: path to a JSONL golden set, or an already-loaded sequence.
        retriever: async ``(query, collection, limit) -> list[dict]``. When
            ``None`` the live in-process engine is used (requires Qdrant).
        top_k: number of results to request per query.
        k_values: rank cutoffs to report (default 5 and 10).
        limit: optional cap on the number of golden rows (smoke subset).

    Returns:
        A ``BacktestReport``. ``status`` is ``"unavailable"`` when every query
        errored (backend down), ``"empty"`` when the corpus has no rows, else
        ``"ok"``. Never raises for a missing backend.
    """
    k_tuple = tuple(sorted(set(k_values)))
    examples = load_golden(corpus) if isinstance(corpus, (str, Path)) else list(corpus)
    if limit is not None:
        examples = examples[:limit]

    report = BacktestReport(total=len(examples), k_values=k_tuple)
    if not examples:
        report.status = "empty"
        return report

    if retriever is None:
        retriever = _live_retriever()

    per_query = [await score_query(retriever, ex, top_k, k_tuple) for ex in examples]
    report.per_query = per_query
    valid = [q for q in per_query if not q.error]
    report.errors = len(per_query) - len(valid)

    if not valid:
        report.status = "unavailable"
        return report

    _aggregate(report, valid, k_tuple)
    return report


def _aggregate(report: BacktestReport, valid: list[QueryScore], k_values: tuple[int, ...]) -> None:
    """Mean metrics and nearest-rank latency percentiles over scored queries."""
    for k in k_values:
        report.mean_recall[k] = sum(q.recall.get(k, 0.0) for q in valid) / len(valid)
        report.mean_ndcg[k] = sum(q.ndcg.get(k, 0.0) for q in valid) / len(valid)
    report.mean_mrr = sum(q.mrr_value for q in valid) / len(valid)

    latencies = [q.latency_ms for q in valid if q.latency_ms > 0]
    if latencies:
        report.p50_latency_ms = percentile(latencies, 50)
        report.p95_latency_ms = percentile(latencies, 95)


def _live_retriever() -> Callable[..., Any]:
    """Build the default live retriever (in-process search engine)."""

    async def live(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        from rugbrain.search.engine import search_multi_collection

        return await search_multi_collection(
            query,
            collections=[collection] if collection else None,
            limit=limit,
            rerank=False,
        )

    return live


__all__ = [
    "DEFAULT_GOLDEN",
    "DEFAULT_K_VALUES",
    "BacktestReport",
    "GoldenQuery",
    "QueryScore",
    "direct_qdrant_retriever",
    "item_is_relevant",
    "load_golden",
    "percentile",
    "report_to_json",
    "result_id",
    "run_backtest",
    "run_backtest_sync",
    "score_query",
]
