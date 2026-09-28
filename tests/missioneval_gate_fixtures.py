"""Shared builders for the mission-gate tests.

Extracted so the two gate test modules each stay under the 200-line cap while
sharing ONE definition of "a report that meets every floor". Two definitions
would drift, and a test that builds a non-representative report proves nothing:
the gate checks the global floors as well as the per-class ones, and it fails a
report that omits a scored class entirely.
"""

from __future__ import annotations

from typing import Any

from rugbrain.missioneval.gate import load_baselines
from rugbrain.missioneval.runner import MissionReport

BASELINES = load_baselines()


def docs(*ids_and_text: tuple[str, str]) -> list[dict[str, Any]]:
    """Canned retrieved docs: ``[{"id": ..., "content": ...}, ...]`` in rank order."""
    return [{"id": i, "content": t} for i, t in ids_and_text]


def floors() -> dict[str, float]:
    """One float per class: the floor of that class's FIRST declared metric."""
    return {cls: float(next(iter(f.items()))[1]) for cls, f in BASELINES["per_class"].items()}


def all_classes_at_floors() -> dict[str, dict[str, float]]:
    """Every SCORED class present, each carrying EVERY metric it declares.

    The gate reads a metric absent from the report as 0.0, so a class whose floor
    sits on ``hit_rate`` must carry ``hit_rate`` too — otherwise the gate
    correctly reports a regression that the caller never intended.
    """
    out: dict[str, dict[str, float]] = {}
    for cls, spec in BASELINES["per_class"].items():
        out[cls] = {"n": 8, **{m: float(v) for m, v in spec.items()}}
    return out


def report(
    cls: str = "technical_analysis",
    class_value: float | None = None,
    *,
    n: int = 8,
) -> MissionReport:
    """A report shaped like a real one: every scored class present, one variable.

    The top-level metrics are pinned to the GLOBAL floors, because the gate
    checks those too. Only ``cls`` moves. Otherwise dropping one class below its
    floor would trip two checks at once and a test could not tell which fired.
    """
    g = BASELINES["global"]
    per_class = all_classes_at_floors()
    if class_value is not None:
        per_class[cls] = {"n": n, "gold_rate": class_value, "hit_rate": 1.0}
    return MissionReport(
        total=n * len(per_class),
        errors=0,
        status="ok",
        gold_rate=float(g.get("gold_rate", 1.0)),
        hit_rate=float(g.get("hit_rate", 1.0)),
        mrr=float(g.get("mrr", 1.0)),
        per_class=per_class,
    )


__all__ = ["BASELINES", "all_classes_at_floors", "docs", "floors", "report"]
