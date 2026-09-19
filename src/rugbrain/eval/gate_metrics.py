"""Metric resolution + verdict formatting helpers for eval gates.

Split out of gates.py to satisfy the shrink-only size ratchet.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rugbrain.eval.harness import BacktestReport

if TYPE_CHECKING:
    from rugbrain.eval.gates import GateVerdict

logger = logging.getLogger(__name__)

# Metrics that are "higher is better" (quality) vs "lower is better" (latency).
_QUALITY_METRICS = ("recall", "ndcg", "mrr")
_LATENCY_METRICS = ("p95_latency_ms",)

def _metric_value(report: BacktestReport, metric: str) -> float | None:
    """Resolve a metric key like ``recall@10`` / ``mrr`` / ``p95_latency_ms``."""
    if metric == "mrr":
        return report.mean_mrr
    if metric == "p95_latency_ms":
        return report.p95_latency_ms
    if "@" in metric:
        name, _, k_str = metric.partition("@")
        try:
            k = int(k_str)
        except ValueError:
            logger.debug("gates: non-integer k in metric %r", metric)
            return None
        if name == "recall":
            return report.mean_recall.get(k)
        if name == "ndcg":
            return report.mean_ndcg.get(k)
    return None


def format_verdict(verdict: GateVerdict) -> str:
    """Human-readable gate table for CI logs."""
    lines = [
        f"eval gates — status={verdict.status} passed={verdict.passed} skipped={verdict.skipped}",
        f"  {'metric':<18}{'baseline':>10}{'actual':>10}{'delta':>10}  result",
    ]
    for r in verdict.results:
        result = (
            f"SKIP ({r.reason})" if r.skipped else ("PASS" if r.passed else f"FAIL ({r.reason})")
        )
        lines.append(
            f"  {r.metric:<18}{r.baseline:>10.4f}{r.actual:>10.4f}{r.delta:>+10.4f}  {result}"
        )
    return "\n".join(lines)
