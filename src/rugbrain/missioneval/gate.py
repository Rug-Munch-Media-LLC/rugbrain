"""
Hard mission gate — FAILS the build when crime retrieval regresses.

Why this exists: the existing nightly eval ran
``errors:50/50, gate.passed:true, skipped:true`` — a 100%-error run **exited
0** because "backend unavailable" was treated as a skip. And the baseline it
checked was 40/50 code-retrieval. Together those two facts meant nothing was
ever actually gated about the mission.

This gate is deliberately unforgiving:

* ``status == "unavailable"`` (every query errored) is a **FAILURE**, not a
  skip. Absent infrastructure is not evidence of quality.
* Fewer than ``MIN_CASES`` scored rows is a **FAILURE** — a gate over three
  rows measures nothing.
* Quality floors are *shrink-only* ratchet values (``missions.baselines.json``).
  Raising a floor is an explicit, reviewable edit; lowering one requires the
  same discipline as any other ratchet.
* Per-class floors exist for the mission's weakest classes, so a collapse in
  ``technical_analysis`` cannot be averaged away by ``exploit_knowledge``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rugbrain.missioneval.runner import MissionReport

BASELINES_PATH = Path(__file__).resolve().parent / "mission_baselines.json"

# A run with fewer scored cases than this cannot gate anything.
MIN_CASES = 10


@dataclass
class Failure:
    """One failed gate condition."""

    check: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.check}: {self.detail}"


@dataclass
class MissionVerdict:
    """Aggregate gate verdict for one mission-eval run."""

    passed: bool = True
    failures: list[Failure] = field(default_factory=list)
    status: str = "ok"
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status,
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "failures": [{"check": f.check, "detail": f.detail} for f in self.failures],
        }


def load_baselines(path: Path | str = BASELINES_PATH) -> dict[str, Any]:
    """Load the shrink-only mission quality floors."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"mission baselines not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def evaluate_mission(
    report: MissionReport,
    baselines: dict[str, Any] | None = None,
) -> MissionVerdict:
    """Compare a mission report against the floors; any miss is a hard failure."""
    floors = baselines if baselines is not None else load_baselines()
    verdict = MissionVerdict(status=report.status)
    verdict.metrics = {
        "gold_rate": report.gold_rate,
        "hit_rate": report.hit_rate,
        "mrr": report.mrr,
        "errors": float(report.errors),
    }

    if report.status == "empty":
        verdict.failures.append(Failure("empty", "no mission cases ran"))
        verdict.passed = False
        return verdict

    if report.status == "unavailable" or report.errors >= report.total:
        verdict.failures.append(
            Failure(
                "unavailable",
                f"{report.errors}/{report.total} queries errored — a broken run is not a pass",
            )
        )
        verdict.passed = False
        return verdict

    scored = report.total - report.errors
    if scored < MIN_CASES:
        verdict.failures.append(
            Failure("too_few_cases", f"{scored} scored < MIN_CASES={MIN_CASES}")
        )
        verdict.passed = False

    for metric, floor in floors.get("global", {}).items():
        actual = verdict.metrics.get(metric, 0.0)
        if actual < float(floor):
            verdict.failures.append(
                Failure(metric, f"actual={actual:.4f} < floor={float(floor):.4f}")
            )
            verdict.passed = False

    for cls, cls_floors in floors.get("per_class", {}).items():
        got = report.per_class.get(cls)
        if got is None:
            verdict.failures.append(
                Failure(f"per_class.{cls}", "class missing from the report — no coverage")
            )
            verdict.passed = False
            continue
        for metric, floor in cls_floors.items():
            actual = float(got.get(metric, 0.0))
            if actual < float(floor):
                verdict.failures.append(
                    Failure(f"per_class.{cls}.{metric}", f"actual={actual:.4f} < floor={floor}")
                )
                verdict.passed = False
    return verdict


def format_verdict(verdict: MissionVerdict) -> str:
    """Human-readable one-block summary for CI logs."""
    lines = [f"mission-eval: {'PASS' if verdict.passed else 'FAIL'} (status={verdict.status})"]
    for name, value in sorted(verdict.metrics.items()):
        lines.append(f"  {name:12s} {value:.4f}")
    for failure in verdict.failures:
        lines.append(f"  FAIL {failure}")
    return "\n".join(lines)


__all__ = [
    "BASELINES_PATH",
    "MIN_CASES",
    "Failure",
    "MissionVerdict",
    "evaluate_mission",
    "format_verdict",
    "load_baselines",
]
