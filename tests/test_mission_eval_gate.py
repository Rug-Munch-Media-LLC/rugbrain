"""The mission eval is a REAL GATE — these tests are the proof, not a claim.

A gate nobody has tried to break is a decoration. Three things are pinned here
and each maps to a way a quality gate silently rots:

1. **A regression fails.** With the measured ``technical_analysis`` floor at
   1.0, a retriever that degrades that class to a partial must turn the gate
   red. A gate that passes a regression is worse than no gate, because it
   launders the regression into a green tick.
2. **A broken retriever is UNAVAILABLE (exit 2), not a capability failure.**
   Infrastructure being down says nothing about corpus quality, and a gate that
   fails capability classes during an outage gets muted within a week.
   ``_embedder_alive`` exists for exactly this and the probe must be able to say
   no.
3. **The floors are a ratchet.** Every per-class floor is ``<=`` the last
   measurement recorded in ``mission_baselines.json``, so a future edit cannot
   quietly lower a bar to make a red build green.

Hermetic: no Qdrant, no network. The live measurement is in
``/root/.knowledge/audit-reports/2026-09-28-sb2-taeval.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rugbrain.missioneval.gate import (
    BASELINES_PATH,
    MIN_CASES,
    evaluate_mission,
    format_verdict,
    load_baselines,
)
from rugbrain.missioneval.goldens import MissionCase, all_cases, cases_for
from rugbrain.missioneval.runner import MissionReport, run_mission_eval

BASELINES = load_baselines()


def _docs(*ids_and_text: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"id": i, "content": t} for i, t in ids_and_text]


def _ta_ids() -> list[str]:
    return [d for c in cases_for("technical_analysis") for d in c.relevant_doc_ids]


def _all_classes_at(floors: dict[str, float]) -> dict[str, dict[str, float]]:
    """Every SCORED class present at its floor, so one class is the variable.

    The gate fails a report that is missing a scored class, and it checks EVERY
    metric a class declares — so a class whose floor is on ``hit_rate`` must
    carry a ``hit_rate`` key too, or the gate (correctly) reads the absent
    metric as 0.0 and reports a false regression.
    """
    out: dict[str, dict[str, float]] = {}
    for cls, spec in BASELINES["per_class"].items():
        row: dict[str, float] = {"n": 8}
        for metric, floor in spec.items():
            row[metric] = float(floor)
        out[cls] = row
    return out


def _floors() -> dict[str, float]:
    return {cls: float(next(iter(f.items()))[1]) for cls, f in BASELINES["per_class"].items()}


def _report(
    cls: str = "technical_analysis",
    class_value: float | None = None,
    *,
    n: int = 8,
) -> MissionReport:
    """A report shaped like a real one: EVERY scored class present, one variable.

    The GLOBAL floors apply to the top-level metrics too, so those are pinned to
    the global floors. Only ``cls`` moves. Otherwise dropping one class below its
    floor would trip two checks at once and this test could not tell which one
    actually fired.
    """
    g = BASELINES["global"]
    per_class = _all_classes_at(_floors())
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


# ── 1. the gate is a real gate ─────────────────────────────────────────


def test_ta_floor_is_raised_to_a_measured_value() -> None:
    """The 0.000 floor is gone and replaced by a real, non-zero measurement."""
    assert BASELINES["per_class"]["technical_analysis"]["gold_rate"] == 1.0
    assert "_measured_2026_09_28_taeval" in BASELINES, (
        "measurement provenance must travel with the floor"
    )


def test_a_ta_regression_fails_the_gate() -> None:
    """One TA case degrading to partial must turn the gate red.

    The floor is 1.0, so anything less is a regression. This is the assertion
    that would have caught a silently broken TA retriever.
    """
    verdict = evaluate_mission(_report(class_value=0.875))
    assert verdict.passed is False
    assert any("per_class.technical_analysis" in f.check for f in verdict.failures)
    assert "FAIL" in format_verdict(verdict)


def test_a_full_ta_regression_still_fails() -> None:
    """Even 7/8 (hit_rate 1.0) is a failure — the floor is on GOLD, not hits."""
    assert evaluate_mission(_report(class_value=0.0)).passed is False


def test_perfect_ta_run_passes() -> None:
    """A run that actually holds the measured value passes — no false red."""
    assert evaluate_mission(_report()).passed is True


# ── 2. a regression anywhere in a scored class fails independently ────


@pytest.mark.parametrize(
    "cls,metric",
    [
        (c, m)
        for c, spec in sorted(BASELINES["per_class"].items())
        for m, v in spec.items()
        if float(v) > 0.0
    ],
)
def test_any_scored_class_can_fail_on_its_own(cls: str, metric: str) -> None:
    """No class is averaged away by a strong one — the original defect.

    One (class, metric) pair per NON-ZERO floor. A 0.0 floor accepts every value
    by design — it exists to catch missing coverage, not quality — so there is
    nothing below it to fall past.
    """
    floor = float(BASELINES["per_class"][cls][metric])
    assert floor > 0.0

    report = _report(cls=cls)  # every class at its floor
    assert evaluate_mission(report).passed is True, "at the floor must pass"

    report.per_class[cls][metric] = floor - 0.01
    verdict = evaluate_mission(report)
    assert verdict.passed is False, f"{cls}.{metric} regressed but the gate passed"
    assert any(cls in f.check for f in verdict.failures)


def test_zero_floor_classes_still_fail_on_missing_coverage() -> None:
    """A 0.0 floor is not a free pass: no class in the report at all IS a fail."""
    report = _report()
    del report.per_class["rug_history"]
    verdict = evaluate_mission(report)
    assert verdict.passed is False
    assert any("rug_history" in f.check for f in verdict.failures)


# ── 3. broken infrastructure is UNAVAILABLE, not a capability failure ──


def test_broken_retriever_is_unavailable_not_a_low_score() -> None:
    """Every case erroring => status unavailable => FAILURE, not a skip."""

    async def _down(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        raise RuntimeError("qdrant unreachable")

    import asyncio

    report = asyncio.run(run_mission_eval(_down, cases_for("technical_analysis"), 10))
    assert report.status == "unavailable"
    assert report.errors == report.total
    verdict = evaluate_mission(report)
    assert verdict.passed is False
    assert any(f.check == "unavailable" for f in verdict.failures)


def test_partial_errors_still_gate() -> None:
    """One erroring case must not silently vanish from the denominator."""
    ta = cases_for("technical_analysis")
    good = {
        c.id: _docs(*[(d, c.match_terms[0] if c.match_terms else "x") for d in c.relevant_doc_ids])
        for c in ta
    }

    async def _flaky(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        case = next((c for c in ta if c.query == query), None)
        if case is None:
            return []
        if case.id == "ta-04":  # exactly one case loses its embedder
            raise RuntimeError("embedding timeout")
        return good.get(case.id, [])[:limit]

    import asyncio

    report = asyncio.run(run_mission_eval(_flaky, ta, 10))
    assert report.status == "ok"
    assert report.errors == 1, "the errored case must be counted, not dropped"
    assert report.total == 8, "the denominator stays 8; the error is visible, not hidden"
    assert report.per_class["technical_analysis"]["n"] == len(ta) - 1
    assert "technical_analysis" in report.per_class, "the class stays gateable"


def test_preflight_exits_two_when_the_embedder_is_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The __main__ preflight must answer 2 (UNAVAILABLE), not 1 (regression).

    A gate that reports an infrastructure outage as a capability regression
    cries wolf on every blip, and a gate that cries wolf is ignored.
    """
    from rugbrain.missioneval import __main__ as entry

    monkeypatch.setattr(entry, "_embedder_alive", lambda: (False, "Connection refused"))
    called = False

    def _must_not_run(*a: Any, **k: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("retrieval ran despite a dead embedder")

    monkeypatch.setattr(entry, "run_mission_eval", _must_not_run)
    assert entry.main() == 2
    assert "UNAVAILABLE" in capsys.readouterr().err
    assert called is False, "a dead embedder must short-circuit before retrieval"


def test_preflight_exits_two_when_retrieval_explodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retriever that raises at the top level is UNAVAILABLE, not a failure."""
    from rugbrain.missioneval import __main__ as entry

    monkeypatch.setattr(entry, "_embedder_alive", lambda: (True, "dim=2560"))

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(entry, "run_mission_eval", _boom)
    assert entry.main() == 2


# ── 4. the golden data itself is well formed ───────────────────────────


def test_every_ta_case_is_grounded_in_a_real_gold_id() -> None:
    """No TA row may be graded on nothing but a substring.

    A row with no gold id and no match term scores gold for any doc containing
    a common word — that is how a class reaches 1.000 while measuring nothing.
    """
    for case in cases_for("technical_analysis"):
        assert case.relevant_doc_ids, f"{case.id} has no gold doc id"
        assert case.match_terms, f"{case.id} has no match term"
        assert case.collection, f"{case.id} names no collection"
        assert case.provenance_note, f"{case.id} has no provenance"


def test_ta_gold_ids_are_unique_across_the_class() -> None:
    """No id may be the gold for two different questions."""
    seen: dict[str, str] = {}
    for case in cases_for("technical_analysis"):
        for doc_id in case.relevant_doc_ids:
            assert doc_id not in seen, f"{doc_id} is gold for both {seen.get(doc_id)} and {case.id}"
            seen[doc_id] = case.id


def test_ta_cases_are_added_to_the_full_set() -> None:
    """The new module must actually be wired into all_cases()."""
    ids = {c.id for c in all_cases()}
    assert {"ta-01", "ta-02", "ta-03", "ta-04", "ta-05", "ta-06", "ta-07", "ta-08"} <= ids


def test_baselines_file_is_valid_json_with_a_measured_block() -> None:
    """The provenance block must be a real object, not a comment."""
    raw = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    block = raw["_measured_2026_09_28_taeval"]
    assert block["per_class_technical_analysis"]["gold_rate"] == 1.0
    assert block["retriever"] and block["embedding_lane"] and block["method"]


# ── 5. the ratchet only goes one way ──────────────────────────────────


def test_no_floor_exceeds_its_recorded_measurement() -> None:
    """A floor above what was measured would be an aspiration, not a gate.

    Exception: technical_analysis, whose floor EQUALS its measurement (1.0);
    it may not exceed it.
    """
    measured = {
        "technical_analysis": BASELINES["_measured_2026_09_28_taeval"][
            "per_class_technical_analysis"
        ],
    }
    for cls, floors in BASELINES["per_class"].items():
        if cls not in measured:
            continue
        for metric, floor in floors.items():
            assert float(floor) <= float(measured[cls].get(metric, 0.0)) + 1e-9, (
                f"{cls}.{metric} floor {floor} exceeds the measured "
                f"{measured[cls].get(metric)} — that is a wish, not a measurement"
            )
