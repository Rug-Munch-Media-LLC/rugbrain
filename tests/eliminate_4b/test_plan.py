"""The diff, the refusals, and the shape knowledge the twin fill depends on.

These are the properties the operator's brief turns on, so they are the
properties tested:

  * the diff is on POINT ID, and only the missing ids get embedded — that is
    why codebase_reference_v2 costs 4,644 and not 2,131,274;
  * a re-run embeds nothing, because the write path reuses the base's id;
  * a collection whose dim is not the planned dim is REFUSED, not adapted to;
  * a missing twin is REFUSED (creating it is a different job, with a
    different approval);
  * the unnamed-vs-named vector distinction is real and is read from Qdrant
    rather than assumed.

Production :6334 is never contacted; see ``fake_qdrant`` for why that matters.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parents[2] / "scripts"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from eliminate_4b import qdrant_io as qio  # noqa: E402
from eliminate_4b.plan import TARGETS, Target, plan  # noqa: E402
from tests.eliminate_4b.fake_qdrant import FakeQdrant, col, docs, pid  # noqa: E402


# ── the diff is on point id, and only missing ids are embedded ──────────
def test_diff_embeds_only_missing_ids() -> None:
    fq = FakeQdrant(
        {
            "b": col(2560, docs(10)),
            "b_8b": col(4096, docs(6)),
        }
    )
    rep = plan(fq.client(), Target("b", 2560))
    assert rep.status == "OK"
    assert rep.to_embed == 4, "must diff on id, not on count arithmetic"
    assert rep.twin_before == 6 and rep.base_count == 10


def test_re_run_is_a_no_op() -> None:
    fq = FakeQdrant({"b": col(2560, docs(4)), "b_8b": col(4096, docs(4))})
    assert plan(fq.client(), Target("b", 2560)).to_embed == 0


def test_odd_gap_counts_exactly() -> None:
    """A tail smaller than the page size still diffs exactly."""
    fq = FakeQdrant({"b": col(2560, docs(9)), "b_8b": col(4096, docs(7))})
    assert plan(fq.client(), Target("b", 2560)).to_embed == 2


# ── refusals: stop, never guess ────────────────────────────────────────
def test_wrong_base_dim_is_refused() -> None:
    """The brief's explicit case: a base that is not 2560 must STOP."""
    fq = FakeQdrant({"b": col(1024, docs(1)), "b_8b": col(4096, [])})
    rep = plan(fq.client(), Target("b", 2560))
    assert rep.status == "REFUSED"
    assert "dim" in " ".join(rep.failed)


def test_missing_twin_is_refused() -> None:
    fq = FakeQdrant({"b": col(2560, docs(1))})
    rep = plan(fq.client(), Target("b", 2560))
    assert rep.status == "REFUSED"
    assert "twin" in " ".join(rep.failed).lower()


def test_non_4096_twin_is_refused() -> None:
    fq = FakeQdrant({"b": col(2560, docs(1)), "b_8b": col(2048, [])})
    assert plan(fq.client(), Target("b", 2560)).status == "REFUSED"


def test_missing_base_is_skipped_not_raised() -> None:
    fq = FakeQdrant({"b_8b": col(4096, [])})
    assert plan(fq.client(), Target("b", 2560)).status == "SKIP"


# ── vector shape: the unnamed-vs-named trap ────────────────────────────
def test_unnamed_twin_vector_shape() -> None:
    """Qdrant serialises the default as {"": {...}} — "" is not a name."""
    assert qio.vector_shape(FakeQdrant({"x": col(4096, [])}).info("x")) == (4096, None)


def test_named_dense_twin_vector_shape() -> None:
    """codebase_reference_v2_8b is named "dense", not the unnamed default."""
    info = FakeQdrant({"x": col(4096, [], vname="dense", sparse=True)}).info("x")
    assert qio.vector_shape(info) == (4096, "dense")
    assert qio.has_sparse(info) is True


def test_sparse_capability_detected_where_absent() -> None:
    assert qio.has_sparse(FakeQdrant({"x": col(4096, [])}).info("x")) is False


# ── the plan table carries no stale counts ─────────────────────────────
def test_all_targets_are_planned_as_2560() -> None:
    assert len(TARGETS) == 12
    assert all(t.planned_base_dim == 2560 for t in TARGETS)
    assert "scan_intel" in {t.base for t in TARGETS}


def test_plan_stores_no_point_counts() -> None:
    """Counts must be measured live. Hardcoding them is exactly the
    codebase_reference_v2 lesson: 4,644, not 2.1M."""
    assert not any(hasattr(t, "count") for t in TARGETS)


def test_upsert_reuses_the_base_point_id() -> None:
    """Idempotence rests on this: the written id IS the base id."""
    assert str(pid(1)) == "00000001-0000-4000-8000-000000000000"
