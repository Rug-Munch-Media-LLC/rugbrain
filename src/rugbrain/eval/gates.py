"""
Retrieval-quality regression gates — fail when metrics drop below baseline.

Compares a :class:`~rugbrain.eval.harness.BacktestReport` against a committed
``baselines.json`` and returns a structured verdict. The gate is *shrink-only*
for quality metrics (a metric may not fall below ``baseline - tolerance``) and
*raise-only* for latency (p95 may not exceed ``baseline * factor``).

Baseline semantics (documented in ``docs/EVALS.md``):

* ``baselines.json`` holds one entry per metric key, e.g.
  ``{"recall@10": {"value": 0.72, "tolerance": 0.03}}``.
* A metric with no baseline entry is **not gated** (reported as ``skipped``) —
  new metrics can be added to the harness without breaking CI.
* Raising a baseline is an explicit, reviewable edit to ``baselines.json`` in
  the same PR that improves retrieval. Lowering a baseline requires a written
  justification in the PR body (the file is the audit trail).
* ``status="unavailable"`` (backend down) is a **skip**, not a failure, so an
  offline CI runner does not go red on infrastructure absence. The smoke
  script decides whether a skip is acceptable.

No LLM-judge dependency: gates operate purely on the numeric report.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rugbrain.eval.baseline_select import (
    FULL_BASELINES,
    select_baseline_path,
)
from rugbrain.eval.gate_metrics import (
    _LATENCY_METRICS,
    _metric_value,
)
from rugbrain.eval.gate_metrics import (
    format_verdict as format_verdict,
)
from rugbrain.eval.harness import BacktestReport

logger = logging.getLogger(__name__)

# Default baseline file shipped with the package (full set).
DEFAULT_BASELINES = FULL_BASELINES

# Metrics that are "higher is better" (quality) vs "lower is better" (latency).

@dataclass
class GateResult:
    """Outcome of one metric comparison."""

    metric: str
    baseline: float
    actual: float
    tolerance: float
    passed: bool
    skipped: bool = False
    reason: str = ""

    @property
    def delta(self) -> float:
        return self.actual - self.baseline


@dataclass
class GateVerdict:
    """Aggregate gate verdict over all configured metrics."""

    passed: bool = True
    skipped: bool = False
    status: str = "ok"  # ok | unavailable | empty
    results: list[GateResult] = field(default_factory=list)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed and not r.skipped]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "skipped": self.skipped,
            "status": self.status,
            "results": [
                {
                    "metric": r.metric,
                    "baseline": round(r.baseline, 4),
                    "actual": round(r.actual, 4),
                    "delta": round(r.delta, 4),
                    "tolerance": r.tolerance,
                    "passed": r.passed,
                    "skipped": r.skipped,
                    "reason": r.reason,
                }
                for r in self.results
            ],
        }


def load_baselines(path: Path | str = DEFAULT_BASELINES) -> dict[str, dict[str, float]]:
    """Load the baseline JSON (``{metric: {value, tolerance}}``)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"baselines not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"baselines must be a JSON object, got {type(data).__name__}")
    return data




def evaluate_gates(
    report: BacktestReport,
    baselines: dict[str, dict[str, float]] | Path | str | None = None,
    *,
    limit: int | None = None,
    total: int | None = None,
) -> GateVerdict:
    """
    Compare *report* against *baselines* and return a pass/fail verdict.

    Args:
        report: the backtest report to gate.
        baselines: baseline dict, or path to a baseline JSON. When ``None``
            the baseline matching the selected query set is used: pass
            ``limit`` (and optionally ``total``) so a smoke run is gated
            against ``baselines.smoke.json`` and a full run against
            ``baselines.full.json``. With no ``limit`` the full baseline is
            used.
        limit: the ``limit`` used for the run (``None`` = all rows).
        total: number of golden rows available, when known.

    Returns:
        A ``GateVerdict``. ``skipped=True`` when the report is unavailable
        (backend down) or empty — the caller decides if that is acceptable.
        A metric absent from the baselines is reported as skipped, not failed.
    """
    if baselines is None:
        baselines = select_baseline_path(limit, total)
    if isinstance(baselines, (str, Path)):
        baselines = load_baselines(baselines)

    verdict = GateVerdict(status=report.status)
    if report.status in ("unavailable", "empty"):
        verdict.skipped = True
        verdict.passed = True
        verdict.results.append(
            GateResult(
                metric="*",
                baseline=0.0,
                actual=0.0,
                tolerance=0.0,
                passed=True,
                skipped=True,
                reason=f"report status={report.status} (no gate applied)",
            )
        )
        return verdict

    for metric, spec in baselines.items():
        if metric.startswith("_") or not isinstance(spec, dict):
            # Metadata keys (``_comment``, ``_source``) are not metrics.
            continue
        baseline = float(spec.get("value", 0.0))
        tolerance = float(spec.get("tolerance", 0.0))
        actual = _metric_value(report, metric)
        if actual is None:
            verdict.results.append(
                GateResult(
                    metric=metric,
                    baseline=baseline,
                    actual=0.0,
                    tolerance=tolerance,
                    passed=True,
                    skipped=True,
                    reason="metric not present in report",
                )
            )
            continue

        if metric in _LATENCY_METRICS:
            # Raise-only: actual may not exceed baseline * (1 + tolerance).
            threshold = baseline * (1.0 + tolerance)
            passed = actual <= threshold
            reason = "" if passed else f"latency {actual:.1f}ms > {threshold:.1f}ms"
        else:
            # Shrink-only: actual may not fall below baseline - tolerance.
            threshold = baseline - tolerance
            passed = actual >= threshold
            reason = "" if passed else f"{metric} {actual:.4f} < {threshold:.4f}"

        verdict.results.append(
            GateResult(
                metric=metric,
                baseline=baseline,
                actual=actual,
                tolerance=tolerance,
                passed=passed,
                reason=reason,
            )
        )

    verdict.passed = not verdict.failures
    return verdict
