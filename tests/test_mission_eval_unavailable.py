"""A broken retriever is UNAVAILABLE — not a capability failure.

The historical defect this pins: a 100%-error eval run used to exit 0 because
"backend unavailable" was treated as a skip. The inverse defect is just as
dangerous and much quieter: if an infrastructure outage is reported as a
capability regression, the gate cries wolf on every blip, operators mute it
within a week, and a real regression later is ignored too.

So there must be exactly three answers, never two:

    0  every measured metric is at or above its floor
    1  a metric regressed, or the run errored
    2  the infrastructure is down and no score is meaningful

These tests hold the 2. The companion module
``test_mission_eval_gate.py`` holds the 0-vs-1 distinction.

Hermetic: the live embedder is never contacted; the probe is exercised once
unmocked in ``test_probe_really_reports_a_dead_lane``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rugbrain.missioneval.gate import evaluate_mission
from rugbrain.missioneval.goldens import all_cases, cases_for
from rugbrain.missioneval.runner import run_mission_eval
from tests.missioneval_gate_fixtures import docs

# ── a dead retriever is not a low score ──────────────────────────────


def test_every_case_erroring_is_unavailable_not_a_skip() -> None:
    """100% errors => status unavailable => FAILURE. Never a pass, never a skip."""

    async def _down(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        raise RuntimeError("qdrant unreachable")

    report = asyncio.run(run_mission_eval(_down, cases_for("technical_analysis"), 10))
    assert report.status == "unavailable"
    assert report.errors == report.total
    verdict = evaluate_mission(report)
    assert verdict.passed is False
    assert any(f.check == "unavailable" for f in verdict.failures)


def test_one_erroring_case_is_counted_not_dropped() -> None:
    """A single failure must stay visible in the denominator, not vanish."""
    ta = cases_for("technical_analysis")
    canned = {
        c.id: docs(*[(d, c.match_terms[0] if c.match_terms else "x") for d in c.relevant_doc_ids])
        for c in ta
    }

    async def _flaky(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        case = next((c for c in ta if c.query == query), None)
        if case is None:
            return []
        if case.id == "ta-04":  # exactly one case loses its embedder
            raise RuntimeError("embedding timeout")
        return canned.get(case.id, [])[:limit]

    report = asyncio.run(run_mission_eval(_flaky, ta, 10))
    assert report.status == "ok", "a partial outage is still a scoreable run"
    assert report.errors == 1
    assert report.total == 8, "the denominator keeps the errored case"
    assert report.per_class["technical_analysis"]["n"] == len(ta) - 1
    assert "technical_analysis" in report.per_class, "the class stays gateable"


# ── the preflight must be able to say "no" ───────────────────────────


def test_preflight_exits_two_when_the_embedder_is_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dead embedder => exit 2, and retrieval must NOT run.

    Short-circuiting before retrieval matters: an eval that queries a corpus
    with a dead embedder records a clean miss per case, which is a capability
    failure wearing an outage's clothes.
    """
    from rugbrain.missioneval import __main__ as entry

    monkeypatch.setattr(entry, "_embedder_alive", lambda: (False, "Connection refused"))
    ran = False

    def _must_not_run(*a: Any, **k: Any) -> Any:
        nonlocal ran
        ran = True
        raise AssertionError("retrieval ran despite a dead embedder")

    monkeypatch.setattr(entry, "run_mission_eval", _must_not_run)
    assert entry.main() == 2
    assert "UNAVAILABLE" in capsys.readouterr().err
    assert ran is False, "a dead embedder must short-circuit before retrieval"


def test_preflight_exits_two_when_retrieval_explodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retriever raising at the top level is UNAVAILABLE, not a failure."""

    from rugbrain.missioneval import __main__ as entry

    monkeypatch.setattr(entry, "_embedder_alive", lambda: (True, "dim=2560"))

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(entry, "run_mission_eval", _boom)
    assert entry.main() == 2


def test_probe_really_reports_a_dead_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unmocked: the probe must answer False when the embedder really fails.

    This is the one test that touches the network, and it only proves a real
    failure is surfaced, which cannot pass by accident.

    It used to break the probe by pointing QWEN_EMBED_URL at a closed port.
    That no longer works BY DESIGN: the seam fix (6c24960) moved the probe onto
    `embed_query`, the single embedder seam, precisely so the retired local
    :8004 lane cannot make a healthy system look dead. So the probe is now
    broken the way a real outage breaks it - an invalid credential against the
    live provider - and must still answer False rather than a healthy verdict.
    """
    from rugbrain.missioneval import __main__ as entry

    monkeypatch.setenv("DEEPINFRA_API_KEY", "definitely-not-a-valid-key")
    alive, detail = entry._embedder_alive()
    assert alive is False, f"probe reported healthy against a failing embedder: {detail}"
    assert detail.strip(), "a failure must carry a reason an operator can act on"


# ── the golden data is grounded, not decorative ───────────────────────


def test_every_ta_case_is_grounded_in_a_real_gold_id() -> None:
    """No TA row may be graded on a substring alone.

    A row with no gold id and no match term scores gold for any document
    containing a common word. That is how a class reaches 1.000 while measuring
    nothing, so it is asserted against explicitly.
    """
    for case in cases_for("technical_analysis"):
        assert case.relevant_doc_ids, f"{case.id} has no gold doc id"
        assert case.match_terms, f"{case.id} has no match term"
        assert case.collection, f"{case.id} names no collection"
        assert case.provenance_note, f"{case.id} records no provenance"


def test_ta_gold_ids_are_unique_across_the_class() -> None:
    """No id may be the gold answer to two different questions."""
    seen: dict[str, str] = {}
    for case in cases_for("technical_analysis"):
        for doc_id in case.relevant_doc_ids:
            assert doc_id not in seen, f"{doc_id} is gold for both {seen.get(doc_id)} and {case.id}"
            seen[doc_id] = case.id


def test_ta_cases_are_wired_into_the_full_set() -> None:
    """The new module must actually be reachable from all_cases()."""
    ids = {c.id for c in all_cases()}
    assert {"ta-01", "ta-02", "ta-03", "ta-04", "ta-05", "ta-06", "ta-07", "ta-08"} <= ids
