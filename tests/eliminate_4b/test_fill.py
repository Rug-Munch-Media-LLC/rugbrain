"""The write path: idempotence, restart safety, and sparse preservation.

The invariants under test, all of which were bugs this project has had:

  * a re-run over a filled twin embeds NOTHING and writes nothing;
  * an interrupted run leaves a partially-filled twin that the next run
    completes, because points are upserted under the base's own ids and the
    diff is recomputed from live state rather than from a stale plan;
  * a sparse-bearing twin receives sparse vectors, so hybrid RRF keeps
    working for the newly written points;
  * the provenance keys the existing twins already carry are stamped on.

DeepInfra is faked; the fake returns 4096-dim, matching the canonical lane.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

_PKG = Path(__file__).resolve().parents[2] / "scripts"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from eliminate_4b import qdrant_io as qio
from eliminate_4b.embed import hash_of_text
from eliminate_4b.fill import fill
from eliminate_4b.plan import plan
from eliminate_4b.targets import CANONICAL_DIM, Target

from tests.eliminate_4b.fake_qdrant import FakeQdrant, col, docs, pid


def _deepinfra() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(__import__("json").loads(request.content)["input"])
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [0.1] * CANONICAL_DIM} for i in range(n)]}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _run(fq: FakeQdrant, base: str, **kw) -> object:
    c = fq.client()
    rep = plan(c, Target(base, 2560))
    fill(c, _deepinfra(), rep, "key", **kw)
    return rep


def test_fill_reaches_parity_and_reports_counts() -> None:
    fq = FakeQdrant({"b": col(2560, docs(6)), "b_8b": col(4096, docs(2))})
    rep = _run(fq, "b")
    assert rep.embedded == 4
    assert rep.twin_after >= rep.base_count, "parity is the pass condition"
    assert rep.parity is True


def test_second_run_embeds_nothing() -> None:
    """Idempotence: the diff is recomputed, so a full twin is a no-op."""
    fq = FakeQdrant({"b": col(2560, docs(6)), "b_8b": col(4096, docs(2))})
    _run(fq, "b")
    fq.upserts.clear()
    again = _run(fq, "b")
    assert again.to_embed == 0 and again.embedded == 0
    assert fq.upserts == [], "a no-op run must not write"


def test_partial_twin_is_completed_not_restarted() -> None:
    """Crash mid-run, then re-run: only the tail is embedded."""
    fq = FakeQdrant({"b": col(2560, docs(10)), "b_8b": col(4096, docs(4))})
    first = _run(fq, "b", limit=3)
    assert first.embedded == 3
    assert not first.parity, "a limited run is deliberately short"
    second = _run(fq, "b")
    assert second.embedded == 3, "only the 3 still-missing ids, not all 6"
    assert second.parity is True


def test_written_points_carry_provenance() -> None:
    fq = FakeQdrant({"b": col(2560, docs(3)), "b_8b": col(4096, [])})
    _run(fq, "b")
    written = [p for _, pts in fq.upserts for p in pts]
    assert len(written) == 3
    for p in written:
        pl = p["payload"]
        assert pl["reembed_lane"] == "deepinfra"
        assert pl["reembedded_from"] == "b"
        assert pl["embedding_dim"] == CANONICAL_DIM
        assert pl["embedding_model"].endswith("8B")


def test_unnamed_twin_gets_the_unnamed_vector_field() -> None:
    fq = FakeQdrant({"b": col(2560, docs(2)), "b_8b": col(4096, [])})
    _run(fq, "b")
    written = [p for _, pts in fq.upserts for p in pts]
    assert all(set(p["vector"]) == {""} for p in written)
    assert all(len(next(iter(p["vector"].values()))) == CANONICAL_DIM for p in written)


def test_sparse_twin_receives_sparse_vectors() -> None:
    """Otherwise the 4,644 new codebase_reference points vanish from hybrid RRF."""
    base = [
        {
            "id": pid(i),
            "payload": {"content": "x"},
            "vector": {"dense": [0.0], "sparse": {"indices": [1, 2], "values": [0.5, 0.9]}},
        }
        for i in range(3)
    ]
    fq = FakeQdrant({"b": col(2560, base), "b_8b": col(4096, [], vname="dense", sparse=True)})
    _run(fq, "b")
    written = [p for _, pts in fq.upserts for p in pts]
    assert len(written) == 3
    for p in written:
        assert set(p["vector"]) == {"dense", "sparse"}
        assert p["vector"]["sparse"] == {"indices": [1, 2], "values": [0.5, 0.9]}


def test_dense_only_twin_gets_no_sparse() -> None:
    fq = FakeQdrant({"b": col(2560, docs(2)), "b_8b": col(4096, [])})
    _run(fq, "b")
    written = [p for _, pts in fq.upserts for p in pts]
    assert all("sparse" not in p["vector"] for p in written)


def test_derived_points_are_flagged_as_derived() -> None:
    """Text rebuilt from payload fields must be auditable in the twin."""
    base = []
    for i in range(2):
        pl = {
            "library_id": "internal/x",
            "file_path": f"a/{i}.py",
            "function_name": "go",
            "title": "go()",
            "description": "d",
            "code_block": "cb",
        }
        pl["content_hash"] = hash_of_text(f"internal/x a/{i}.py:go\n\ngo()\n\nd\n\ncb")
        base.append({"id": pid(i), "payload": pl})
    fq = FakeQdrant({"b": col(2560, base), "b_8b": col(4096, [])})
    rep = _run(fq, "b")
    written = [p for _, pts in fq.upserts for p in pts]
    assert rep.embedded == 2 and rep.unrecoverable == 0
    assert all(p["payload"]["text_source"] == "derived_from_content_hash" for p in written)


def test_unrecoverable_points_are_skipped_and_counted_not_filled() -> None:
    """A scan_intel-shaped point is skipped, counted, and parity is FAILED."""
    base = [{"id": pid(0), "payload": {"chain": "solana", "address": "abc", "risk_level": "high"}}]
    fq = FakeQdrant({"b": col(2560, base), "b_8b": col(4096, [])})
    rep = _run(fq, "b")
    assert rep.embedded == 0 and rep.unrecoverable == 1
    assert fq.upserts == [], "nothing embeddable means nothing written"
    assert rep.parity is False and rep.status == "FAILED"
    assert "no recoverable embed input" in " ".join(rep.failed)


def test_natively_texted_points_are_not_flagged() -> None:
    fq = FakeQdrant({"b": col(2560, docs(2)), "b_8b": col(4096, [])})
    _run(fq, "b")
    written = [p for _, pts in fq.upserts for p in pts]
    assert all("text_source" not in p["payload"] for p in written)


def test_upserted_point_count_matches_embedded() -> None:
    fq = FakeQdrant({"b": col(2560, docs(5)), "b_8b": col(4096, docs(1))})
    rep = _run(fq, "b")
    assert rep.embedded == 4
    assert qio.count(fq.client(), "b_8b") == 5
