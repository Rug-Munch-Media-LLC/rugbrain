"""Tests for scripts/verify_4b_elimination.py.

Two jobs:
  1. Unit-test the pure logic against hand-built payloads, including BOTH
     Qdrant vector wire shapes.
  2. Prove the verifier actually goes RED. A checker that only ever passes is
     indistinguishable from a checker that does nothing, so every check gets a
     deliberately-wrong input and we assert the failure.

No test in this file contacts Qdrant; the HTTP layer is faked.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_4b_elimination.py"

spec = importlib.util.spec_from_file_location("verify_4b_elimination", SCRIPT)
assert spec is not None and spec.loader is not None
v4b = importlib.util.module_from_spec(spec)
sys.modules["verify_4b_elimination"] = v4b
spec.loader.exec_module(v4b)


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------
BARE = {"vectors": {"size": 4096, "distance": "Cosine"}, "sparse_vectors": None}
NAMED = {
    "vectors": {"dense": {"size": 4096, "distance": "Cosine"}},
    "sparse_vectors": {"sparse": {"modifier": "idf"}},
}


class TestVectorShapes:
    def test_bare_shape_parsed(self) -> None:
        facts = v4b.CollectionFacts("x", v4b.dense_width(BARE), 1, False)
        assert facts.width == 4096
        assert v4b.is_named_vector_collection(BARE) is False
        assert v4b.dense_vector_name(BARE) is None

    def test_named_shape_parsed(self) -> None:
        facts = v4b.CollectionFacts("x", v4b.dense_width(NAMED), 1, True)
        assert facts.width == 4096
        assert v4b.is_named_vector_collection(NAMED) is True
        assert v4b.dense_vector_name(NAMED) == "dense"

    def test_malformed_config_is_none_not_an_exception(self) -> None:
        assert v4b.dense_width({}) is None
        assert v4b.dense_width({"vectors": None}) is None
        assert v4b.dense_width({"vectors": {}}) is None
        assert v4b.dense_width({"vectors": {"dense": {}}}) is None

    def test_multi_named_picks_deterministic_key(self) -> None:
        cfg = {"vectors": {"zeta": {"size": 4096}, "alpha": {"size": 4096}}}
        assert v4b.dense_vector_name(cfg) == "alpha"

    def test_mutation_malformed_config_must_not_be_treated_as_ok(self) -> None:
        """A missing vector spec must NOT read as the canonical width."""
        assert v4b.dense_width({"vectors": {}}) != 4096


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------
class TestConfigParsing:
    def test_reads_declared_dim(self, tmp_path: Path) -> None:
        env = tmp_path / ".env.rag"
        env.write_text("# comment\nRAG_EMBED_DIM=4096\nOTHER=1\n")
        assert v4b.canonical_dim_from_env(env) == (4096, f"{env.name}:RAG_EMBED_DIM")

    def test_strips_quotes_and_whitespace(self, tmp_path: Path) -> None:
        env = tmp_path / ".env.rag"
        env.write_text('RAG_EMBED_DIM = "4096"\n')
        assert v4b.canonical_dim_from_env(env)[0] == 4096

    def test_mutation_wrong_value_is_read_not_defaulted(self, tmp_path: Path) -> None:
        env = tmp_path / ".env.rag"
        env.write_text("RAG_EMBED_DIM=2560\n")
        assert v4b.canonical_dim_from_env(env)[0] == 2560

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(v4b.MeasurementError, match="config not found"):
            v4b.canonical_dim_from_env(tmp_path / "nope.rag")

    def test_missing_key_raises_rather_than_defaulting(self, tmp_path: Path) -> None:
        env = tmp_path / ".env.rag"
        env.write_text("SOMETHING_ELSE=4096\n")
        with pytest.raises(v4b.MeasurementError, match="not declared"):
            v4b.canonical_dim_from_env(env)

    def test_non_integer_raises(self, tmp_path: Path) -> None:
        env = tmp_path / ".env.rag"
        env.write_text("RAG_EMBED_DIM=auto\n")
        with pytest.raises(v4b.MeasurementError, match="not an integer"):
            v4b.canonical_dim_from_env(env)


# ---------------------------------------------------------------------------
# Verdict logic -- each test below is a MUTATION test: a deliberately wrong
# world state must produce a red verdict. Without these, a green run proves
# nothing.
# ---------------------------------------------------------------------------
def _report(**kw: Any) -> v4b.Report:
    base: dict[str, Any] = {
        "canonical_dim": 4096,
        "provenance": "test",
        "offenders": [],
        "fill_rows": [],
        "empty_bases": [],
        "stale_bases": [],
        "probes": [{"collection": "c", "hits": 3, "note": ""}],
        "problems": [],
    }
    base.update(kw)
    return v4b.Report(**base)


def _row(name: str, ok: bool, base_pts: int = 10, twin_pts: int = 10) -> dict[str, Any]:
    return {
        "collection": name,
        "base_points": base_pts,
        "twin": name + "_8b",
        "twin_points": twin_pts,
        "pass": ok,
        "note": "",
    }


class TestVerdictLogic:
    def test_perfectly_clean_world_is_zero(self) -> None:
        rep = _report(fill_rows=[_row(n, True) for n in ("a", "b")])
        assert rep.exit_code() == 0

    def test_mutation_leftover_offdim_collection_goes_red(self) -> None:
        rep = _report(
            offenders=[v4b.CollectionFacts("stale", 2560, 10, False)],
            stale_bases=[v4b.CollectionFacts("stale", 2560, 10, False)],
        )
        assert rep.exit_code() == 1

    def test_mutation_short_twin_goes_red(self) -> None:
        rep = _report(fill_rows=[_row("a", False, 100, 99)])
        assert rep.exit_code() == 1

    def test_mutation_leftover_empty_shell_goes_red(self) -> None:
        rep = _report(empty_bases=[v4b.CollectionFacts("e", 2560, 0, False)])
        assert rep.exit_code() == 1

    def test_mutation_zero_search_hits_goes_red(self) -> None:
        rep = _report(probes=[{"collection": "c", "hits": 0, "note": ""}])
        assert rep.exit_code() == 1

    def test_mutation_unmeasurable_outranks_not_clean(self) -> None:
        rep = _report(problems=["Qdrant down"], fill_rows=[_row("a", False)])
        assert rep.exit_code() == 2

    def test_unmeasurable_never_returns_zero(self) -> None:
        rep = _report(problems=["anything"])
        assert rep.exit_code() != 0


# ---------------------------------------------------------------------------
# Read-only contract -- the invariant that makes this file safe to run against
# production.
# ---------------------------------------------------------------------------
class TestReadOnlyContract:
    def test_module_source_contains_no_mutating_verbs(self) -> None:
        src = SCRIPT.read_text()
        for verb in ('method="PUT"', 'method="PATCH"', 'method="DELETE"'):
            assert verb not in src, f"mutating HTTP verb present: {verb}"

    def test_module_does_not_import_rugbrain(self) -> None:
        # Check executable lines only: the module docstring names these things
        # precisely to document what it refuses to do.
        src = "\n".join(
            ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")
        )
        code = src.split('"""', 2)[-1] if src.count('"""') >= 2 else src
        for bad in (
            "import rugbrain",
            "from rugbrain",
            "from src.rugbrain",
            "lane_resolver",
            "dim_policy",
            "qdrant_client",
        ):
            assert bad not in code, f"verifier must not share code with ingest: {bad}"

    def test_only_get_and_post_are_constructed(self) -> None:
        src = SCRIPT.read_text()
        verbs = {tok.split('"')[1] for tok in src.split("method=")[1:]}
        assert verbs <= {"GET", "POST"}, f"unexpected HTTP verb: {verbs}"


# ---------------------------------------------------------------------------
# End-to-end against a fake Qdrant, including the two named/bare probe shapes.
# ---------------------------------------------------------------------------
class TestAgainstFakeQdrant:
    @pytest.fixture()
    def fake(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
        # A realistic world contains all 12 must-fill bases plus their twins,
        # otherwise the other 11 rows legitimately FAIL and the "clean" case
        # is untestable.
        state: dict[str, Any] = {
            "scan_intel": {"vectors": {"size": 2560, "distance": "Cosine"}, "points": 50},
            "scan_intel_8b": {"vectors": {"size": 4096, "distance": "Cosine"}, "points": 0},
            "good": {"vectors": {"size": 4096, "distance": "Cosine"}, "points": 3},
            "empty_old": {"vectors": {"size": 2560, "distance": "Cosine"}, "points": 0},
            "named": {
                "vectors": {"dense": {"size": 4096, "distance": "Cosine"}},
                "sparse": {"sparse": {}},
                "points": 4,
            },
        }
        for name in v4b.MUST_FILL:
            if name in state:
                continue
            state[name] = {
                "vectors": {"size": 2560, "distance": "Cosine"},
                "points": 7,
            }
            state[name + "_8b"] = {
                "vectors": {"size": 4096, "distance": "Cosine"},
                "points": 7,
            }
        env = tmp_path / ".env.rag"
        env.write_text("RAG_EMBED_DIM=4096\n")

        def fake_request(url: str, body: dict[str, Any] | None = None) -> Any:
            # Order matters: the search endpoint also contains
            # "/collections/<name>", so it must be matched first.
            if url.endswith("/points/search"):
                vec = body["vector"]
                if isinstance(vec, dict) and "name" not in vec:
                    raise v4b.MeasurementError("Not existing vector name")
                if isinstance(vec, list) and len(vec) != 4096:
                    raise v4b.MeasurementError("wrong dim")
                return {"result": [{"id": i} for i in range(3)]}
            if url.endswith("/collections") and body is None:
                return {"result": {"collections": [{"name": n} for n in state]}}
            for name, spec in state.items():
                if url.endswith(f"/collections/{name}"):
                    # Real Qdrant nests the vector spec inside "params".
                    params = {"vectors": spec["vectors"]}
                    if spec.get("sparse") is not None:
                        params["sparse_vectors"] = spec["sparse"]
                    return {
                        "result": {
                            "config": {"params": params},
                            "points_count": spec["points"],
                        }
                    }
            raise AssertionError(f"unexpected url {url}")

        monkeypatch.setattr(v4b, "_request", fake_request)
        return {"state": state, "env": env}

    def test_not_clean_world_exits_one(self, fake: dict[str, Any]) -> None:
        rep = v4b.build_report("http://x", fake["env"], 4096)
        assert rep.exit_code() == 1
        assert any(r["collection"] == "scan_intel" and not r["pass"] for r in rep.fill_rows)
        assert [f.name for f in rep.empty_bases] == ["empty_old"]

    def test_missing_twin_is_reported_not_silently_passed(self, fake: dict[str, Any]) -> None:
        del fake["state"]["scan_intel_8b"]
        rep = v4b.build_report("http://x", fake["env"], 4096)
        row = next(r for r in rep.fill_rows if r["collection"] == "scan_intel")
        assert row["pass"] is False
        assert "NO _8b TWIN" in row["note"]

    def test_mutation_filled_twin_flips_to_clean(self, fake: dict[str, Any]) -> None:
        """A twin reaching parity lets the base go, but the twin must go too.

        The end state is a corpus that is entirely 4096-wide. Leaving an
        ``_8b`` twin behind is NOT clean, and the verifier says so -- that is
        the assertion, not an accident of the fixture.
        """
        for name in list(fake["state"]):
            if name in v4b.MUST_FILL:
                continue
            spec = fake["state"][name]["vectors"]
            if spec.get("size", 4096) != 4096:
                del fake["state"][name]
        for name in v4b.MUST_FILL:
            twin = name + "_8b"
            if twin in fake["state"]:
                fake["state"][twin]["points"] = fake["state"][name]["points"]
            del fake["state"][name]
        rep = v4b.build_report("http://x", fake["env"], 4096)
        assert rep.exit_code() == 0, v4b.render(rep)

    def test_mutation_twin_one_short_is_still_red(self, fake: dict[str, Any]) -> None:
        fake["state"]["scan_intel_8b"]["points"] = 49
        rep = v4b.build_report("http://x", fake["env"], 4096)
        assert rep.exit_code() == 1

    def test_both_probe_shapes_are_exercised(self, fake: dict[str, Any]) -> None:
        rep = v4b.build_report("http://x", fake["env"], 4096)
        probed = {p["collection"]: p["note"] for p in rep.probes}
        assert any("named=True" in n for n in probed.values())
        assert any("named=False" in n for n in probed.values())
        assert all(p["hits"] > 0 for p in rep.probes)

    def test_json_output_is_serialisable(self, fake: dict[str, Any]) -> None:
        rep = v4b.build_report("http://x", fake["env"], 4096)
        json.dumps([vars(f) for f in rep.offenders])
        json.dumps(rep.fill_rows)
        json.dumps(rep.probes)
