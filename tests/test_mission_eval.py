"""Tests for the crypto-crime mission eval (judge, runner, gate, goldens).

No live Qdrant: every retriever here is an in-process fake, so the suite is
hermetic and fast. What IS asserted against the real contract:

* the golden set is graded, verified and covers all seven job classes
* grading is deterministic and does not award gold past the rank cap
* a 100%-error run is a FAILURE, not a skip (the defect being fixed)
* a too-small scored set cannot pass
* per-class floors are enforced independently of the global average
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from rugbrain.missioneval.gate import (
    BASELINES_PATH,
    MIN_CASES,
    evaluate_mission,
    format_verdict,
    load_baselines,
)
from rugbrain.missioneval.golden_cases import CASES
from rugbrain.missioneval.golden_cases_exploit import EXPLOIT_CASES
from rugbrain.missioneval.golden_cases_ta import TA_CASES
from rugbrain.missioneval.goldens import JOB_CLASSES, MissionCase, all_cases, cases_for
from rugbrain.missioneval.judge import GOLD, MISS, PARTIAL, grade_hits
from rugbrain.missioneval.runner import MissionReport, run_mission_eval

# ── fixtures ─────────────────────────────────────────────────────────


def _retriever(pairs: dict[str, list[dict[str, Any]]]):
    """Retriever that returns the canned docs for a case id embedded in the query."""
    cases = {c.id: c for c in all_cases()}

    async def _run(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        case = next((c for c in cases.values() if c.query == query), None)
        if case is None:
            return []
        return pairs.get(case.id, [])[:limit]

    return _run


async def _boom(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
    raise RuntimeError("qdrant down")


def _report(**kw: Any) -> MissionReport:
    """A synthetic run that MEETS every floor in mission_baselines.json.

    Each class is set to its own floor so this represents a passing run; a test
    that wants a regression passes a lower value for one class explicitly.
    """
    floors = load_baselines()["per_class"]
    per_class = {
        cls: {
            "n": 1,
            **{m: float(v) for m, v in spec.items()},
            "hit_rate": max(float(spec.get("hit_rate", 1.0)), 1.0),
        }
        for cls, spec in floors.items()
    }
    base = {
        "total": 23,
        "errors": 0,
        "status": "ok",
        "gold_rate": 0.78,
        "hit_rate": 0.87,
        "mrr": 0.78,
        "per_class": per_class,
    }
    base.update(kw)
    return MissionReport(**base)


# ── golden set contract ───────────────────────────────────────────────


class TestGoldenSet:
    def test_enough_cases_to_gate(self) -> None:
        assert len(all_cases()) >= MIN_CASES

    def test_covers_every_mission_job_class(self) -> None:
        assert {c.job_class for c in all_cases()} == set(JOB_CLASSES)

    def test_ids_are_unique(self) -> None:
        ids = [c.id for c in all_cases()]
        assert len(ids) == len(set(ids))

    def test_every_case_has_query_and_class(self) -> None:
        for c in all_cases():
            assert c.query.strip(), c.id
            assert c.job_class in JOB_CLASSES, c.id

    def test_every_case_has_ground_truth(self) -> None:
        for c in all_cases():
            assert c.relevant_doc_ids or c.match_terms, f"{c.id} has no gold signal"

    def test_rows_are_verified_and_documented(self) -> None:
        for c in all_cases():
            assert c.provenance == "verified", c.id
            assert c.provenance_note.strip(), c.id

    def test_gold_ids_are_not_placeholders(self) -> None:
        for c in all_cases():
            for doc_id in c.relevant_doc_ids:
                assert doc_id not in {"", "0", "TODO"}, f"{c.id} placeholder id"

    def test_gold_ids_are_not_sentinel_addresses(self) -> None:
        # threat_intel_v2 still carries 0xcccc.. test sentinels; a golden row
        # must never point at one.
        for c in all_cases():
            for doc_id in c.relevant_doc_ids:
                assert not doc_id.startswith("00000000-cccc"), c.id

    def test_crime_families_dominate_over_code(self) -> None:
        crime = {"rug_history", "scam_detection", "exploit_knowledge"}
        assert sum(c.job_class in crime for c in all_cases()) >= len(all_cases()) // 2

    def test_cases_for_filters_by_class(self) -> None:
        assert cases_for("exploit_knowledge") == EXPLOIT_CASES
        assert all(c.job_class == "scam_detection" for c in cases_for("scam_detection"))
        assert cases_for(None) == all_cases()

    def test_both_case_modules_import(self) -> None:
        assert len(CASES) + len(EXPLOIT_CASES) + len(TA_CASES) == len(all_cases())


class TestBaselines:
    def test_baselines_file_parses(self) -> None:
        floors = load_baselines(BASELINES_PATH)
        assert "global" in floors
        assert set(floors["per_class"]) <= set(JOB_CLASSES)

    def test_global_floors_are_probabilities(self) -> None:
        for name, value in load_baselines()["global"].items():
            assert 0.0 <= float(value) <= 1.0, name


# ── judge ─────────────────────────────────────────────────────────────


class TestJudge:
    def test_exact_id_is_gold_at_any_rank(self) -> None:
        docs = [{"id": "a"}, {"id": "b"}, {"id": "gold-id", "content": "x"}]
        grades = grade_hits(docs, ["gold-id"], ["ponzi"])
        assert grades[2].grade == GOLD

    def test_match_term_promotes_to_gold(self) -> None:
        docs = [{"id": "z", "content": "Scam entry: ponzi scheme"}]
        assert grade_hits(docs, [], ["ponzi"])[0].grade == GOLD

    def test_partial_term_is_partial_not_gold(self) -> None:
        docs = [{"id": "z", "content": "fake exchange withdrawal deposit"}]
        grades = grade_hits(docs, [], ["ponzi"], partial_terms=["withdrawal"])
        assert grades[0].grade == PARTIAL
        assert grades[0].is_hit and not grades[0].is_gold

    def test_unrelated_is_miss(self) -> None:
        grades = grade_hits([{"id": "z", "content": "unrelated solana news"}], [], ["ponzi"])
        assert grades[0].grade == MISS

    def test_gold_cannot_be_earned_past_the_rank_cap(self) -> None:
        docs = [{"id": str(i), "content": "unrelated"} for i in range(9)]
        docs.append({"id": "late", "content": "ponzi scheme"})
        grades = grade_hits(docs, [], ["ponzi"], gold_rank_cap=5)
        assert grades[-1].grade == MISS

    def test_only_first_gold_counts_later_hits(self) -> None:
        docs = [
            {"id": "a", "content": "ponzi"},
            {"id": "b", "content": "ponzi again"},
        ]
        grades = grade_hits(docs, [], ["ponzi"])
        assert grades[0].grade == GOLD
        assert grades[1].grade == MISS

    def test_empty_retrieval_scores_nothing(self) -> None:
        assert grade_hits([], ["a"], ["ponzi"]) == []

    def test_matching_is_case_insensitive(self) -> None:
        assert grade_hits([{"id": "z", "content": "PONZI"}], [], ["ponzi"])[0].grade == GOLD


# ── runner ────────────────────────────────────────────────────────────


class TestRunner:
    async def test_scores_a_gold_case(self) -> None:
        case = next(c for c in all_cases() if c.id == "scam-01")
        retriever = _retriever({"scam-01": [{"id": case.relevant_doc_ids[0], "content": "ponzi"}]})
        report = await run_mission_eval(retriever, [case])
        assert report.status == "ok"
        assert report.gold_rate == 1.0
        assert report.per_case[0].grade == GOLD

    async def test_miss_when_nothing_relevant(self) -> None:
        case = next(c for c in all_cases() if c.id == "scam-01")
        retriever = _retriever({"scam-01": [{"id": "z", "content": "solana news"}]})
        report = await run_mission_eval(retriever, [case])
        assert report.gold_rate == 0.0
        assert report.per_case[0].grade == MISS

    async def test_all_errors_yields_unavailable_not_ok(self) -> None:
        report = await run_mission_eval(_boom, all_cases())
        assert report.status == "unavailable"
        assert report.errors == report.total

    async def test_empty_case_list(self) -> None:
        report = await run_mission_eval(_boom, [])
        assert report.status == "empty"

    async def test_per_class_breakdown_present(self) -> None:
        case = next(c for c in all_cases() if c.id == "rug-01")
        report = await run_mission_eval(_retriever({}), [case])
        # retriever returns [] (not an error) -> scored, per-class breakdown exists
        assert report.status == "ok"
        assert "rug_history" in report.per_class
        assert report.per_class["rug_history"]["n"] == 1


# ── gate ──────────────────────────────────────────────────────────────


class TestMissionGate:
    def test_measured_baseline_passes(self) -> None:
        assert evaluate_mission(_report()).passed

    def test_regression_fails(self) -> None:
        assert not evaluate_mission(_report(gold_rate=0.10)).passed

    def test_total_error_run_fails_instead_of_skipping(self) -> None:
        # The defect: errors:50/50 used to exit 0. It must be a failure now.
        verdict = evaluate_mission(
            _report(status="unavailable", errors=23, gold_rate=0.0, hit_rate=0.0, mrr=0.0)
        )
        assert not verdict.passed
        assert any(f.check == "unavailable" for f in verdict.failures)

    def test_too_few_scored_cases_fails(self) -> None:
        verdict = evaluate_mission(_report(total=3, errors=0))
        assert not verdict.passed
        assert any(f.check == "too_few_cases" for f in verdict.failures)

    def test_empty_report_fails(self) -> None:
        verdict = evaluate_mission(_report(total=0, status="empty"))
        assert not verdict.passed

    def test_per_class_floor_cannot_be_averaged_away(self) -> None:
        report = _report()
        report.per_class["rug_history"] = {"n": 3, "gold_rate": 0.0, "hit_rate": 0.0}
        verdict = evaluate_mission(report)
        assert not verdict.passed
        assert any("rug_history" in f.check for f in verdict.failures)

    def test_missing_class_is_a_failure(self) -> None:
        report = _report()
        del report.per_class["technical_analysis"]
        verdict = evaluate_mission(report)
        assert not verdict.passed

    def test_floors_can_be_passed_in(self) -> None:
        assert not evaluate_mission(_report(), {"global": {"gold_rate": 0.99}}).passed
        assert evaluate_mission(_report(), {"global": {"gold_rate": 0.5}}).passed

    def test_format_verdict_mentions_fail(self) -> None:
        out = format_verdict(evaluate_mission(_report(gold_rate=0.0)))
        assert "FAIL" in out

    def test_verdict_serialises(self) -> None:
        payload = json.dumps(evaluate_mission(_report()).to_dict())
        assert "passed" in payload


class TestMissionCaseModel:
    def test_rejects_unknown_job_class_only_at_use_site(self) -> None:
        # The model is permissive on purpose; JOB_CLASSES is enforced by tests
        # and by the per_class gate rather than by pydantic.
        case = MissionCase(id="x", job_class="nope", query="q", match_terms=["t"])
        assert case.job_class == "nope"

    def test_defaults_are_empty_not_none(self) -> None:
        case = MissionCase(id="x", job_class="news", query="q")
        assert case.relevant_doc_ids == []
        assert case.match_terms == []
        assert case.partial_terms == []

    def test_duplicate_ids_are_not_rejected_by_model(self) -> None:
        # Documented behaviour: uniqueness is a set-level invariant (tested above).
        a = MissionCase(id="x", job_class="news", query="q")
        assert a.id == MissionCase(id="x", job_class="news", query="q").id


@pytest.mark.parametrize("job_class", list(JOB_CLASSES))
def test_every_class_has_at_least_one_case(job_class: str) -> None:
    assert cases_for(job_class)
