"""Mutation tests for scripts/drop_retired_bases.py.

A safety mechanism that has never been observed refusing is not a safety
mechanism. Every test here drives ``execute()`` against a FAKE Qdrant and
asserts on which DELETEs were issued. The fakes are deliberately adversarial:
the twin is behind, the twin is missing, the width flips to canonical, the
server dies mid-run. If the assertion regresses into a no-op, these fail.

No test touches production Qdrant (:6334). Everything is an in-process fake.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from drop_retired_bases import (
    CANONICAL_DIM,
    Plan,
    QdrantUnreachable,
    Refusal,
    Verdict,
    classify,
    execute,
    main,
    parse_width,
)


class FakeQdrant:
    """In-memory Qdrant. Records every DELETE so refusal is observable."""

    def __init__(self, collections: dict[str, dict[str, Any]]) -> None:
        # name -> {"width": int|None, "points": int}
        self.collections = dict(collections)
        self.deleted: list[str] = []
        self.raise_on_get: set[str] = set()
        self.fail_next_delete = False

    # --- shape of the real server -------------------------------------
    def _get(self, name: str) -> dict[str, Any]:
        if name in self.raise_on_get:
            raise QdrantUnreachable(f"simulated GET failure for {name}")
        return self.collections[name]

    def _fetch(self, url: str) -> Any:
        path = url.split("/", 3)[-1]
        if path == "collections":
            return {"result": {"collections": [{"name": n} for n in sorted(self.collections)]}}
        name = path.split("/", 1)[-1]
        if name not in self.collections:
            raise QdrantUnreachable(f"404 {name}")
        info = self._get(name)
        return {
            "result": {
                "points_count": info["points"],
                "status": "green",
                "config": {"params": {"vectors": {"size": info["width"], "distance": "Cosine"}}},
            }
        }

    def _request(self, url: str, method: str) -> Any:
        if method == "GET":
            return self._fetch(url)
        name = url.rsplit("/", 1)[-1]
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise QdrantUnreachable(f"simulated DELETE failure for {name}")
        self.deleted.append(name)
        self.collections.pop(name, None)
        return {"result": True}

    def install(self, monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
        class _Resp:
            def __init__(self, payload: Any) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return json.dumps(self._payload).encode()

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: Any) -> bool:
                return False

        def fake_urlopen(request: Any, timeout: float = 20.0) -> _Resp:
            return _Resp(self._request(request.full_url, request.get_method()))

        monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeQdrant:
    import drop_retired_bases as module

    q = FakeQdrant({})
    q.install(monkeypatch, module)
    return q


# --------------------------------------------------------------------------
# parse_width: the two Qdrant shapes. An earlier attempt read only one shape
# and reported populated collections as having no vector config.
# --------------------------------------------------------------------------
def test_parse_width_bare_single_vector_shape() -> None:
    assert parse_width({"size": 4096, "distance": "Cosine"}) == 4096


def test_parse_width_named_vector_shape() -> None:
    assert parse_width({"dense": {"size": 4096, "distance": "Cosine"}}) == 4096


def test_parse_width_named_with_sparse_sibling() -> None:
    params = {"dense": {"size": 2560, "distance": "Cosine"}, "sparse": {"modifier": "idf"}}
    assert parse_width(params) == 2560


def test_parse_width_bare_takes_precedence_over_named() -> None:
    # agent_memory shape: bare vectors + non-empty sparse_vectors sibling.
    assert parse_width({"size": 4096, "distance": "Cosine", "on_disk": False}) == 4096


@pytest.mark.parametrize("bad", [None, [], "4096", {}, {"distance": "Cosine"}, 4096])
def test_parse_width_returns_none_when_absent(bad: Any) -> None:
    assert parse_width(bad) is None


# --------------------------------------------------------------------------
# THE MUTATION TESTS: the delete path must refuse.
# --------------------------------------------------------------------------
def test_twin_behind_is_not_deleted(fake: FakeQdrant) -> None:
    """Base holds more than the twin. Refuse, and keep the base."""
    fake.collections = {
        "news_v2": {"width": 2560, "points": 100},
        "news_v2_8b": {"width": 4096, "points": 99},  # ONE behind
    }
    plan = classify("http://fake", empty_shells=[])
    verdict = next(v for v in plan.verdicts if v.name == "news_v2")
    assert verdict.eligible is False
    assert "twin behind" in verdict.reason

    execute("http://fake", plan)
    assert fake.deleted == [], "twin-behind base was deleted — the assertion did not refuse"
    assert "news_v2" in fake.collections


def test_plan_time_assertion_marks_twin_behind_ineligible(fake: FakeQdrant) -> None:
    """Direct guard on the plan-time comparison itself.

    Written after mutation M1 (inverting ``twin.points >= base_points``) SURVIVED
    the suite: the allow-branch sat behind an unreachable ``if`` and no test
    exercised it. This asserts the verdict directly, with no execute() involved,
    so the comparison cannot be weakened without a red test.
    """
    fake.collections = {
        "news_v2": {"width": 2560, "points": 100},
        "news_v2_8b": {"width": 4096, "points": 99},
    }
    verdict = next(
        v for v in classify("http://fake", empty_shells=[]).verdicts if v.name == "news_v2"
    )
    assert verdict.eligible is False
    assert verdict.twin_points == 99
    assert verdict.base_points == 100
    assert "99 < 100" in verdict.reason


@pytest.mark.parametrize(
    ("twin_points", "expected_eligible"),
    [(99, False), (100, True), (101, True)],
)
def test_plan_time_assertion_boundary(
    fake: FakeQdrant, twin_points: int, expected_eligible: bool
) -> None:
    """Boundary table: one behind fails, equal passes, one ahead passes.

    Covers both outcomes of the single assertion branch, so neither the ``>``
    nor the ``>=`` form can survive by accident.
    """
    fake.collections = {
        "wallet_v2": {"width": 2560, "points": 100},
        "wallet_v2_8b": {"width": 4096, "points": twin_points},
    }
    verdict = next(
        v for v in classify("http://fake", empty_shells=[]).verdicts if v.name == "wallet_v2"
    )
    assert verdict.eligible is expected_eligible, f"twin={twin_points} vs base=100"


def test_empty_shell_is_not_also_reported_as_twinless(fake: FakeQdrant) -> None:
    """A Group B shell has no twin by design; it must not appear twice.

    Regression: a declared empty shell was reported both as an eligible PASS
    ("nothing to lose") and in the REFUSED list as "twin absent from Qdrant" —
    two contradictory verdicts for one collection.
    """
    fake.collections = {"hyperliquid": {"width": 1024, "points": 0}}
    plan = classify("http://fake", empty_shells=["hyperliquid"])
    assert [v.name for v in plan.eligible] == ["hyperliquid"]
    assert plan.refused == []
    assert "hyperliquid" not in {v.name for v in plan.verdicts if not v.eligible}


def test_twin_missing_is_not_deleted(fake: FakeQdrant) -> None:
    """No _8b twin at all, and the base holds points. Refuse."""
    fake.collections = {"rug_events": {"width": 2560, "points": 1877}}
    plan = classify("http://fake", empty_shells=[])
    refused = next(v for v in plan.refused if v.name == "rug_events")
    assert "twin absent" in refused.reason

    execute("http://fake", plan)
    assert fake.deleted == [], "twinless base was deleted"


def test_twin_behind_appears_only_at_delete_time(fake: FakeQdrant) -> None:
    """The live-writer race: twin leads at plan time, falls behind before DELETE.

    This is the exact reason the execute path re-reads instead of trusting the
    dry run. A build that caches the dry-run verdict deletes this collection.
    """
    fake.collections = {
        "scam_intel_v2": {"width": 2560, "points": 100},
        "scam_intel_v2_8b": {"width": 4096, "points": 100},
    }
    plan = classify("http://fake", empty_shells=[])
    assert next(v for v in plan.verdicts if v.name == "scam_intel_v2").eligible is True

    # Ingest stalls the twin between plan and delete.
    fake.collections["scam_intel_v2_8b"]["points"] = 99

    execute("http://fake", plan)
    assert fake.deleted == [], "stale plan verdict was trusted; deleted without re-assertion"
    verdict = next(v for v in plan.verdicts if v.name == "scam_intel_v2")
    assert verdict.eligible is False
    assert "behind at delete time" in verdict.reason


def test_canonical_4096_is_never_a_candidate(fake: FakeQdrant) -> None:
    """A 4096 collection is skipped even when it has a twin and 0 points."""
    fake.collections = {
        "standards": {"width": 4096, "points": 0},
        "standards_8b": {"width": 4096, "points": 0},
    }
    plan = classify("http://fake", empty_shells=["standards"])
    assert plan.verdicts == [], "4096 appeared as a deletable candidate"
    assert any(r.name == "standards" for r in plan.refused)
    execute("http://fake", plan)
    assert fake.deleted == []
    assert CANONICAL_DIM == 4096


def test_empty_shell_holding_points_is_refused(fake: FakeQdrant) -> None:
    """A declared empty shell that has since accumulated points must not vanish."""
    fake.collections = {"audit_findings": {"width": 2560, "points": 42}}
    plan = classify("http://fake", empty_shells=["audit_findings"])
    assert plan.verdicts == []
    assert "not empty" in plan.refused[0].reason
    execute("http://fake", plan)
    assert fake.deleted == []


def test_unparseable_width_is_refused(fake: FakeQdrant) -> None:
    """A width we cannot read is UNKNOWN, never a licence to delete."""
    fake.collections = {
        "mystery": {"width": None, "points": 5},
        "mystery_8b": {"width": 4096, "points": 9},
    }
    plan = classify("http://fake", empty_shells=[])
    # width None is not in RETIRABLE_DIMS so it is skipped, never planned.
    assert [v.name for v in plan.verdicts if v.name == "mystery"] == []
    assert "mystery" in fake.collections


def test_dropped_when_twin_covers_exactly(fake: FakeQdrant) -> None:
    """Equal counts pass. Delta 0 is not a near-miss."""
    fake.collections = {
        "contract_audits": {"width": 2560, "points": 902},
        "contract_audits_8b": {"width": 4096, "points": 902},
    }
    plan = classify("http://fake", empty_shells=[])
    execute("http://fake", plan)
    assert fake.deleted == ["contract_audits"]
    assert "contract_audits_8b" in fake.collections


def test_dropped_when_twin_ahead(fake: FakeQdrant) -> None:
    fake.collections = {
        "social_intel": {"width": 2560, "points": 17286},
        "social_intel_8b": {"width": 4096, "points": 20980},
    }
    plan = classify("http://fake", empty_shells=[])
    execute("http://fake", plan)
    assert fake.deleted == ["social_intel"]


def test_empty_shell_with_no_twin_is_dropped(fake: FakeQdrant) -> None:
    """Group B: no twin to assert against, and genuinely 0 points."""
    fake.collections = {"hyperliquid": {"width": 1024, "points": 0}}
    plan = classify("http://fake", empty_shells=["hyperliquid"])
    execute("http://fake", plan)
    assert fake.deleted == ["hyperliquid"]


def test_qdrant_unreachable_mid_run_is_not_a_pass(fake: FakeQdrant) -> None:
    """A dead server must skip, not silently mark deleted."""
    fake.collections = {
        "news_v2": {"width": 2560, "points": 10},
        "news_v2_8b": {"width": 4096, "points": 10},
    }
    plan = classify("http://fake", empty_shells=[])
    fake.raise_on_get.add("news_v2_8b")
    execute("http://fake", plan)
    verdict = next(v for v in plan.verdicts if v.name == "news_v2")
    assert verdict.deleted is False
    assert "unreachable" in verdict.reason


def test_classify_raises_when_server_is_gone() -> None:
    from drop_retired_bases import classify as real_classify

    with pytest.raises(QdrantUnreachable):
        real_classify("http://127.0.0.1:1", empty_shells=[], timeout=0.2)


def test_refusal_carries_its_reason() -> None:
    exc = Refusal("news_v2", "twin behind at delete time: 9 < 10")
    assert exc.name == "news_v2"
    assert "9 < 10" in exc.reason


# --------------------------------------------------------------------------
# CLI guards
# --------------------------------------------------------------------------
def test_execute_without_yes_refuses_and_deletes_nothing(
    fake: FakeQdrant, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--execute", "--qdrant-url", "http://fake", "--empty-shell", "hyperliquid"])
    assert rc == 1
    assert fake.deleted == []
    assert "requires --yes" in capsys.readouterr().err


def test_dry_run_is_the_default_and_deletes_nothing(
    fake: FakeQdrant, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.collections = {
        "news_v2": {"width": 2560, "points": 161054},
        "news_v2_8b": {"width": 4096, "points": 166383},
        "hyperliquid": {"width": 1024, "points": 0},
    }
    rc = main(["--qdrant-url", "http://fake", "--empty-shell", "hyperliquid"])
    out = capsys.readouterr().out
    assert rc == 0
    assert fake.deleted == []
    assert "nothing was deleted" in out
    assert "161054" in out and "166383" in out
    assert "reclaimable" in out


def test_dry_run_exits_nonzero_when_a_candidate_fails(
    fake: FakeQdrant, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.collections = {
        "scan_intel": {"width": 2560, "points": 52360},
        "scan_intel_8b": {"width": 4096, "points": 0},
    }
    rc = main(["--qdrant-url", "http://fake"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" in out
    assert "twin behind" in out
    assert fake.deleted == []


def test_json_plan_reports_reclaimable(
    fake: FakeQdrant, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.collections = {
        "wallet_v2": {"width": 2560, "points": 9657},
        "wallet_v2_8b": {"width": 4096, "points": 9657},
    }
    main(["--qdrant-url", "http://fake", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["reclaimable"] == 9657
    assert payload["dropped"] == 0
    assert fake.deleted == []


def test_plan_reclaimable_excludes_failed_verdicts() -> None:
    plan = Plan(
        verdicts=[
            Verdict("good", 2560, 100, "good_8b", 100, True, "ok"),
            Verdict("bad", 2560, 999, "bad_8b", 1, False, "twin behind"),
        ]
    )
    assert plan.reclaimable == 100, "a failed candidate's points leaked into the tally"
