"""Tests for the eval verification state machine, dim preflight and ratchet.

These tests assert the GATES, never the data. In particular nothing here
asserts that a golden row is verified: verification is a human judgement, and a
test that demands it manufactures the judgement it is meant to check. What is
tested is that the state is a function of the evidence sidecar, so it cannot be
inflated by editing the golden set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.eval import verification as verify
from rugbrain.eval.baseline_ratchet import (
    EXIT_BASELINE_MISMATCH,
    EXIT_COULD_NOT_MEASURE,
    EXIT_DIM_SPLIT,
    EXIT_MEANING,
    EXIT_PASS,
    EXIT_REGRESSED,
    LayerMetrics,
    Measurement,
    evaluate,
    load_baseline,
    write_baseline,
)
from rugbrain.eval.dim_preflight import extract_store_dim
from rugbrain.eval.ratchet_runner import corpus_hash
from rugbrain.eval.registry_contract import SOURCES_DIR, scan_all, scan_module

GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "rugbrain"
    / "eval"
    / "golden"
    / "crypto_rag_golden.jsonl"
)


# ── verification state machine ───────────────────────────────────────


class TestVerification:
    def test_row_id_is_stable_and_content_addressed(self) -> None:
        assert verify.row_id("hello") == verify.row_id("  hello  ")
        assert verify.row_id("hello") != verify.row_id("world")

    def test_absent_sidecar_means_nothing_verified(self, tmp_path: Path) -> None:
        # The honest default: no evidence on file is zero verified rows, not
        # an error and not an implicit pass.
        assert verify.load_evidence(tmp_path / "nope.json") == {}

    def test_thin_evidence_cannot_verify(self) -> None:
        # ids present but no reviewer/date: not a completed verification.
        thin = verify.Evidence(row_id="g-1", collection="c", observed_ids=["a"])
        assert verify.decide(thin) is verify.Decision.UNVERIFIED
        # complete record verifies
        full = verify.Evidence(
            row_id="g-1",
            collection="c",
            observed_ids=["a"],
            reviewer="me",
            confirmed_at="2026-09-28",
        )
        assert verify.decide(full) is verify.Decision.VERIFIED

    def test_ids_alone_do_not_verify(self) -> None:
        # A verified row with no ids cannot be scored, so it was never checked.
        rec = verify.Evidence(
            row_id="g-1", collection="c", observed_ids=[], reviewer="me", confirmed_at="2026-09-28"
        )
        assert not rec.is_sufficient
        assert verify.decide(rec) is verify.Decision.UNVERIFIED

    def test_counts_reports_human_backlog(self) -> None:
        queries = ["a", "b", "c"]
        evidence = {
            verify.row_id("a"): verify.Evidence(
                row_id=verify.row_id("a"),
                collection="c",
                observed_ids=["1"],
                reviewer="r",
                confirmed_at="2026-09-28",
            )
        }
        tally = verify.counts(queries, evidence)
        assert tally["total"] == 3
        assert tally["verified"] == 1
        assert tally["needs_human"] == 2

    def test_worklist_is_bounded(self) -> None:
        queries = [f"q{i}" for i in range(100)]
        items = verify.worklist(queries, {}, limit=5)
        assert len(items) == 5
        assert all("missing" in i for i in items)

    def test_sidecar_roundtrip_and_duplicate_rejection(self, tmp_path: Path) -> None:
        rec = verify.Evidence(
            row_id="g-1",
            collection="c",
            observed_ids=["1"],
            reviewer="r",
            confirmed_at="2026-09-28",
        )
        payload = {
            "schema": verify.EVIDENCE_SCHEMA,
            "records": [
                {
                    "row_id": rec.row_id,
                    "collection": rec.collection,
                    "observed_ids": rec.observed_ids,
                    "reviewer": rec.reviewer,
                    "confirmed_at": rec.confirmed_at,
                }
            ],
        }
        p = tmp_path / "verification.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert verify.load_evidence(p)["g-1"].observed_ids == ["1"]

        payload["records"].append(dict(payload["records"][0]))
        p.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate"):
            verify.load_evidence(p)

    def test_schema_mismatch_is_refused(self, tmp_path: Path) -> None:
        p = tmp_path / "verification.json"
        p.write_text(json.dumps({"schema": 999, "records": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="schema mismatch"):
            verify.load_evidence(p)


# ── the two red tests, reframed around machinery ─────────────────────


class TestGoldenVerificationIsHonest:
    def test_shipped_set_is_fully_accounted_for(self) -> None:
        # Every shipped row must fall into exactly one decision. This is the
        # invariant that replaced the false "all rows verified" claim.
        from rugbrain.eval.ratchet_runner import load_rows

        queries = [r["query"] for r in load_rows(GOLDEN)]
        evidence = verify.load_evidence(GOLDEN.parent / verify.EVIDENCE_FILENAME)
        tally = verify.counts(queries, evidence)
        assert (
            tally["verified"] + tally["unverified"] + tally["pending"] + tally["rejected"]
            == tally["total"]
        )
        assert tally["total"] > 0

    def test_no_row_claims_verified_without_evidence(self) -> None:
        # The regression guard for the original bug: a row may only be
        # 'verified' if an evidence record backs it. Editing the golden set to
        # add provenance="verified" cannot make this pass.
        from rugbrain.eval.ratchet_runner import load_rows

        evidence = verify.load_evidence(GOLDEN.parent / verify.EVIDENCE_FILENAME)
        for row in load_rows(GOLDEN):
            state = verify.decide(evidence.get(verify.row_id(row["query"])))
            if state is verify.Decision.VERIFIED:
                assert evidence[verify.row_id(row["query"])].is_sufficient


# ── dimension preflight ──────────────────────────────────────────────


class TestDimPreflight:
    def test_extract_named_vectors(self) -> None:
        dim, how = extract_store_dim(
            {"config": {"params": {"vectors": {"dense": {"size": 4096, "distance": "Cosine"}}}}}
        )
        assert dim == 4096
        assert "dense" in how

    def test_extract_bare_vectors(self) -> None:
        # Qdrant's legacy unnamed shape. Dict-shaped, so it must be matched
        # before the named-vector branch or every pre-migration collection
        # would be reported as a false split.
        dim, how = extract_store_dim(
            {"config": {"params": {"vectors": {"size": 2560, "distance": "Cosine"}}}}
        )
        assert dim == 2560
        assert "unnamed" in how

    def test_mixed_widths_are_not_silently_collapsed(self) -> None:
        # Two named vectors of different widths must not resolve to one value;
        # a preflight that picked the first would miss a real split.
        dim, how = extract_store_dim(
            {"config": {"params": {"vectors": {"a": {"size": 2560}, "b": {"size": 4096}}}}}
        )
        assert dim == 0
        assert "MIXED" in how

    def test_absent_config_is_unknown_not_zero_dim(self) -> None:
        dim, how = extract_store_dim({})
        assert dim == 0
        assert "absent" in how


# ── baseline ratchet ─────────────────────────────────────────────────


def _m(corpus: str = "abc", **kw) -> Measurement:
    """A measurable baseline. ``measured>0`` is required or the layer reads as
    unmeasured, which is deliberately exit 2 — so fixtures must set it."""
    defaults = dict(
        layers={
            "hybrid": LayerMetrics(
                recall_at_5=0.80, recall_at_10=0.90, mrr=0.70, ndcg_at_10=0.75, measured=50
            )
        },
        case_count=50,
    )
    defaults.update(kw)
    return Measurement(corpus_hash=corpus, **defaults)


class TestRatchet:
    def test_pass_within_tolerance(self) -> None:
        cur = _m(
            layers={
                "hybrid": LayerMetrics(
                    recall_at_5=0.79, recall_at_10=0.89, mrr=0.68, ndcg_at_10=0.73, measured=50
                )
            }
        )
        assert evaluate(cur, _m()).exit_code == EXIT_PASS

    def test_regression_is_exit_1_with_attribution(self) -> None:
        cur = _m(
            layers={
                "hybrid": LayerMetrics(
                    recall_at_5=0.50, recall_at_10=0.60, mrr=0.40, ndcg_at_10=0.50, measured=50
                )
            }
        )
        v = evaluate(cur, _m())
        assert v.exit_code == EXIT_REGRESSED
        assert v.regressions
        # every regression names its layer so the drop is attributable
        assert all(r.layer == "hybrid" for r in v.regressions)
        assert {r.metric for r in v.regressions} >= {"recall_at_5", "mrr"}

    def test_no_baseline_is_could_not_measure_never_pass(self) -> None:
        v = evaluate(_m(), None)
        assert v.exit_code == EXIT_COULD_NOT_MEASURE
        assert not v.ok

    def test_unmeasured_layer_is_not_a_passing_layer(self) -> None:
        cur = _m(layers={"hybrid": LayerMetrics(measured=0, errors=50)})
        v = evaluate(cur, _m())
        assert v.exit_code == EXIT_COULD_NOT_MEASURE

    def test_baseline_hash_mismatch_is_exit_3(self) -> None:
        v = evaluate(_m(corpus="def"), _m(corpus="abc"))
        assert v.exit_code == EXIT_BASELINE_MISMATCH

    def test_dimension_split_wins_over_reported_regression(self) -> None:
        # When dims disagree the recall numbers are meaningless; reporting a
        # regression verdict would be a lie about why quality moved.
        cur = _m(
            layers={"hybrid": LayerMetrics(recall_at_5=0.10, measured=50)},
            dim_checks=[{"collection": "c", "ok": False, "detail": "split"}],
        )
        v = evaluate(cur, _m())
        assert v.exit_code == EXIT_DIM_SPLIT

    def test_all_five_exit_codes_are_distinct(self) -> None:
        codes = [
            EXIT_PASS,
            EXIT_REGRESSED,
            EXIT_COULD_NOT_MEASURE,
            EXIT_BASELINE_MISMATCH,
            EXIT_DIM_SPLIT,
        ]
        assert len(set(codes)) == 5
        assert set(codes) == set(EXIT_MEANING)

    def test_baseline_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "b.json"
        write_baseline(p, _m())
        loaded = load_baseline(p)
        assert loaded is not None
        assert loaded.corpus_hash == "abc"
        assert loaded.layers["hybrid"].recall_at_5 == 0.80

    def test_missing_baseline_file_is_none(self, tmp_path: Path) -> None:
        assert load_baseline(tmp_path / "absent.json") is None


# ── corpus fingerprint ───────────────────────────────────────────────


class TestCorpusHash:
    def test_reorder_does_not_change_hash(self) -> None:
        rows = [{"query": "a", "gold_chunks": ["x"]}, {"query": "b", "gold_chunks": []}]
        assert corpus_hash(rows) == corpus_hash(list(reversed(rows)))

    def test_gold_edit_changes_hash(self) -> None:
        a = [{"query": "a", "gold_chunks": ["x"]}]
        b = [{"query": "a", "gold_chunks": ["y"]}]
        assert corpus_hash(a) != corpus_hash(b)

    def test_adding_a_case_changes_hash(self) -> None:
        a = [{"query": "a", "gold_chunks": []}]
        b = [*a, {"query": "b", "gold_chunks": []}]
        assert corpus_hash(a) != corpus_hash(b)


# ── registry contract ────────────────────────────────────────────────


class TestRegistryContract:
    def test_sources_dir_exists(self) -> None:
        # Guards the silent-zero bug: a wrong path scanned nothing and
        # reported a clean registry.
        assert SOURCES_DIR.is_dir(), f"ingest_sources not found at {SOURCES_DIR}"

    def test_scythe_radar_collective_violation_is_detected(self) -> None:
        # The concrete case the per-source check missed: declares rug_events,
        # writes known_scams. Owned by another lane for the fix; this asserts
        # the detector sees it so the violation cannot go quiet.
        v = scan_module(SOURCES_DIR / "scythe_radar_datasets.py")
        assert v is not None
        assert v.declared == "rug_events"
        assert "known_scams" in v.emitted

    def test_helper_modules_are_not_violations(self) -> None:
        # False positives bury real signal, so helpers with no Source class
        # must be clean.
        assert scan_module(SOURCES_DIR / "fetch_state.py") is None
        assert scan_module(SOURCES_DIR / "channel_taxonomy.py") is None

    def test_scan_covers_many_modules(self) -> None:
        # A scan that reads almost nothing is a gate that cannot fail.
        assert len(list(SOURCES_DIR.glob("*.py"))) > 40
        scan_all()  # must not raise
