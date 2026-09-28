"""The mission eval is a REAL GATE — regressions must turn it red.

A gate nobody has tried to break is a decoration, and a gate that passes a
regression is worse than no gate: it launders the regression into a green tick.
These tests attack the gate from the side that matters — the measured
``technical_analysis`` floor, now 1.0.

Live measurement and provenance: see
``/root/.knowledge/audit-reports/2026-09-28-sb2-taeval.md``.

Hermetic: no Qdrant, no network, no embedder.
"""

from __future__ import annotations

import json

import pytest

from rugbrain.missioneval.gate import BASELINES_PATH, evaluate_mission, format_verdict
from tests.missioneval_gate_fixtures import BASELINES, report

# ── the floor is a measurement, not an aspiration ─────────────────────


def test_ta_floor_is_raised_to_a_measured_value() -> None:
    """The 0.000 floor is gone, replaced by a real non-zero measurement."""
    assert BASELINES["per_class"]["technical_analysis"]["gold_rate"] == 1.0
    assert "_measured_2026_09_28_taeval" in BASELINES, (
        "the measurement that justifies the floor must travel with it"
    )


def test_the_measurement_provenance_is_a_real_object() -> None:
    """A floor with no retrievable record of HOW it was measured is a guess."""
    raw = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    block = raw["_measured_2026_09_28_taeval"]
    assert block["per_class_technical_analysis"]["gold_rate"] == 1.0
    for field in ("retriever", "embedding_lane", "method"):
        assert block.get(field), f"provenance is missing {field!r}"
    # 0 errors is the GOOD value here, so it must be present-and-zero, not truthy.
    assert block["errors"] == 0, "a floor measured on errored cases is not a measurement"
    assert block["total"] > 0


def test_a_ta_regression_fails_the_gate() -> None:
    """One TA case degrading to partial must turn the gate red.

    The floor is 1.0, so anything less is a regression. This is the assertion
    that would catch a silently broken TA retriever.
    """
    verdict = evaluate_mission(report(class_value=0.875))
    assert verdict.passed is False
    assert any("per_class.technical_analysis" in f.check for f in verdict.failures)
    assert "FAIL" in format_verdict(verdict)


def test_a_full_ta_regression_still_fails() -> None:
    """Even 7/8 with hit_rate 1.0 is a failure — the floor is on GOLD, not hits."""
    assert evaluate_mission(report(class_value=0.0)).passed is False


def test_perfect_ta_run_passes() -> None:
    """A run that actually holds the measured value passes — no false red."""
    assert evaluate_mission(report()).passed is True


# ── no class is averaged away by a strong one ─────────────────────────


@pytest.mark.parametrize(
    ("cls", "metric"),
    [
        (c, m)
        for c, spec in sorted(BASELINES["per_class"].items())
        for m, v in spec.items()
        if float(v) > 0.0
    ],
)
def test_any_scored_class_can_fail_on_its_own(cls: str, metric: str) -> None:
    """Each (class, metric) with a non-zero floor is proven gateable alone.

    A 0.0 floor accepts every value by design — it exists to catch MISSING
    COVERAGE, not quality — so there is nothing below it to fall past.
    """
    floor = float(BASELINES["per_class"][cls][metric])
    assert floor > 0.0

    rep = report(cls=cls)  # every class sitting exactly on its floor
    assert evaluate_mission(rep).passed is True, "a run AT the floor must pass"

    rep.per_class[cls][metric] = floor - 0.01
    verdict = evaluate_mission(rep)
    assert verdict.passed is False, f"{cls}.{metric} regressed but the gate passed"
    assert any(cls in f.check for f in verdict.failures)


def test_zero_floor_classes_still_fail_on_missing_coverage() -> None:
    """A 0.0 floor is not a free pass: the class being ABSENT is a hard fail."""
    rep = report()
    del rep.per_class["rug_history"]
    verdict = evaluate_mission(rep)
    assert verdict.passed is False
    assert any("rug_history" in f.check for f in verdict.failures)


def test_missing_any_scored_class_fails() -> None:
    """Coverage is all-or-nothing: a class that never ran is not a pass."""
    for cls in BASELINES["per_class"]:
        rep = report()
        del rep.per_class[cls]
        assert evaluate_mission(rep).passed is False, f"{cls} missing but gate passed"


# ── the ratchet only goes one way ────────────────────────────────────


def test_no_floor_exceeds_its_recorded_measurement() -> None:
    """A floor above a measurement is a wish, not a gate.

    Only technical_analysis carries a per-class measurement block; it may sit AT
    its measurement (1.0) but never above it.
    """
    measured = {
        "technical_analysis": BASELINES["_measured_2026_09_28_taeval"][
            "per_class_technical_analysis"
        ],
    }
    for cls, spec in BASELINES["per_class"].items():
        if cls not in measured:
            continue
        for metric, floor in spec.items():
            recorded = float(measured[cls].get(metric, 0.0))
            assert float(floor) <= recorded + 1e-9, (
                f"{cls}.{metric} floor {floor} exceeds the measured {recorded}"
            )
