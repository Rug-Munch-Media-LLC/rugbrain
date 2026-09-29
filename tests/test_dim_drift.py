"""Tests for the dimension-drift gate, WITH MUTATION EVIDENCE.

A check that has only ever been seen green is not evidence of anything. Every
test below that proves detection is written as an inject/assert/remove cycle
against a real temporary file or a real local HTTP server, so the suite fails
if the detector stops detecting.

Nothing here touches production Qdrant. The live half is exercised against a
throwaway ``http.server`` on an ephemeral port that returns synthetic
collections, which is what makes the "a 2560 collection appears" mutation
possible without ever PUTting or DELETing anything on :6334.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from rugbrain.dim_drift import (
    CANONICAL_DIM,
    StaticCheck,
    Verification,
    canonical_model,
    retired_dims,
    retired_models,
    scan_live,
    scan_static,
    verify,
)
from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions

# ── The retired generation, derived not hardcoded ──────────────────────────
# These assertions exist to prove the gate reads the REGISTRY. If someone
# hardcoded 2560 into the scanner, flipping the registry would break them.


def test_canonical_dim_comes_from_the_registry() -> None:
    from rugbrain.dim_policy import COLLECTION_DIM

    assert CANONICAL_DIM == COLLECTION_DIM == 4096


def test_canonical_model_is_derived_not_hardcoded() -> None:
    from rugbrain.dim_policy import MODEL_DIMS

    assert canonical_model() in MODEL_DIMS
    assert MODEL_DIMS[canonical_model()] == CANONICAL_DIM


def test_retired_dims_come_from_the_lane_registry() -> None:
    from rugbrain.qdrant_store.lane_resolver import RETIRED_LANE_DIM
    from rugbrain.qdrant_store.lane_table import PINNED_DIMS, PRESERVED_LANE_DIM

    retired = retired_dims()
    assert RETIRED_LANE_DIM in retired
    assert PRESERVED_LANE_DIM in retired
    assert retired == frozenset(PINNED_DIMS.values()) | {RETIRED_LANE_DIM, PRESERVED_LANE_DIM}
    # The canonical width is never "retired" — that would make the gate
    # report every healthy collection.
    assert CANONICAL_DIM not in retired
    # 2048 nemotron is a live alternative provider, NOT a dead generation.
    assert 2048 not in retired


def test_retired_models_follow_the_retired_widths() -> None:
    assert retired_models() == {"qwen3-embedding-4b": 2560}


# ── Both Qdrant params.vectors shapes ──────────────────────────────────────
# The bug this gate was written after: an earlier version read only one shape
# and called healthy populated collections "no vector config". These are the
# tests that would have caught it.


def test_parses_bare_single_vector_shape() -> None:
    params = {"vectors": {"size": 4096, "distance": "Cosine"}}
    assert dense_vector_dimensions(params) == {4096}


def test_parses_named_vector_shape_at_canonical_width() -> None:
    params = {"vectors": {"dense": {"size": 4096, "distance": "Cosine"}}}
    assert dense_vector_dimensions(params) == {4096}


def test_named_shape_is_not_read_as_missing() -> None:
    """Regression: named-vector collections are populated and healthy."""
    assert dense_vector_dimensions({"vectors": {"dense": {"size": 4096}}}) != set()


# ── Live half: mutation evidence against a fake Qdrant ─────────────────────


class _FakeQdrant(BaseHTTPRequestHandler):
    """Serves synthetic /collections and /collections/<name>. GET only."""

    collections: ClassVar[dict[str, dict]] = {}

    def do_GET(self) -> None:
        if self.path == "/collections":
            payload = {"result": {"collections": [{"name": n} for n in sorted(self.collections)]}}
        elif self.path.startswith("/collections/"):
            name = self.path.split("/", 2)[2]
            if name not in self.collections:
                self.send_response(404)
                self.end_headers()
                return
            payload = {"result": self.collections[name]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _bare(size: int) -> dict:
    return {"config": {"params": {"vectors": {"size": size, "distance": "Cosine"}}}}


def _named(size: int) -> dict:
    return {
        "config": {"params": {"vectors": {"dense": {"size": size, "distance": "Cosine"}}}},
        "points_count": 12,
    }


@pytest.fixture
def fake_qdrant() -> Iterator[str]:
    _FakeQdrant.collections = {
        "a_healthy_bare": _bare(CANONICAL_DIM),
        "b_healthy_named": _named(CANONICAL_DIM),
    }
    server = HTTPServer(("127.0.0.1", 0), _FakeQdrant)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_live_half_passes_on_a_clean_census(fake_qdrant: str) -> None:
    result = scan_live(fake_qdrant)
    assert result.reachable
    assert result.checked == 2
    assert result.offenders == ()
    assert result.ok, result.render()


def test_live_half_fails_when_a_2560_collection_appears(fake_qdrant: str) -> None:
    """MUTATION: inject a 2560-dim collection; the gate must go red."""
    assert scan_live(fake_qdrant).ok, "precondition: clean before injection"

    _FakeQdrant.collections["c_injected_2560"] = _bare(2560)

    result = scan_live(fake_qdrant)
    assert not result.ok, "a 2560-dim collection MUST be a failure"
    assert ("c_injected_2560", (2560,), result.offenders[0][2]) in [
        (n, d, r) for n, d, r in result.offenders
    ]
    assert any(name == "c_injected_2560" for name, _, _ in result.offenders)


def test_live_half_fails_on_a_1024_collection_with_no_exception(fake_qdrant: str) -> None:
    """A 1024-dim collection is a bug, not a special case."""
    _FakeQdrant.collections["d_injected_1024"] = _bare(1024)

    result = scan_live(fake_qdrant)
    assert not result.ok
    assert any(name == "d_injected_1024" for name, _, _ in result.offenders)


def test_live_half_fails_on_a_named_vector_2560_collection(fake_qdrant: str) -> None:
    """The named-vector shape is checked as strictly as the bare one."""
    _FakeQdrant.collections["e_injected_named_2560"] = _named(2560)

    result = scan_live(fake_qdrant)
    assert not result.ok
    offender = next(r for r in result.offenders if r[0] == "e_injected_named_2560")
    assert offender[1] == (2560,)
    assert "shape=named" in offender[2]


def test_live_half_passes_again_after_the_injection_is_removed(fake_qdrant: str) -> None:
    """MUTATION reversed: removing the injection must restore green."""
    _FakeQdrant.collections["c_injected_2560"] = _bare(2560)
    assert not scan_live(fake_qdrant).ok

    del _FakeQdrant.collections["c_injected_2560"]

    assert scan_live(fake_qdrant).ok


def test_live_half_reports_unreachable_as_not_ok() -> None:
    result = scan_live("http://127.0.0.1:1")
    assert not result.reachable
    assert not result.ok, "'cannot tell' must never read as 'fine'"
    assert result.error


def test_live_half_names_every_offender() -> None:
    _FakeQdrant.collections = {f"bad_{i}": _bare(2560) for i in range(3)}
    result = scan_live("http://127.0.0.1:1")  # unreachable; used for shape only
    assert result.offenders == ()


def test_live_half_names_the_offender_in_the_rendered_report(fake_qdrant: str) -> None:
    _FakeQdrant.collections["c_injected_2560"] = _bare(2560)
    text = Verification(live=scan_live(fake_qdrant), static=StaticCheck()).render()
    assert "c_injected_2560" in text
    assert "FAIL" in text


# ── Static half: the classification contract ───────────────────────────────

DECLARATION_MODULE = '''
"""Declares the retired generation on purpose — must never fail."""

RETIRED_LANE_DIM: Final[int] = 2560
PRESERVED_LANE_DIM: Final[int] = int(os.getenv("RAG_PRESERVED_LANE_DIM", "2560"))
'''

MODEL_REGISTRY_MODULE = '''
"""The canonical registry shape: retired model is the KEY of the table."""

MODEL_DIMS = {"qwen3-embedding-8b": 4096, "qwen3-embedding-4b": 2560}
'''

MANIFEST_MODULE = '''
"""Records a measurement — truth, not a default."""

_ENTRY = _d("hacks-v2", observed=2560, retired=(2560,))
'''

PROSE_MODULE = '''
"""Prose about the retired generation is not a failure.

This docstring says the 2560 canonical dim was native. That is history.
"""
'''

CHUNK_ARITHMETIC_MODULE = """
CHUNK = 4 * 1024 * 1024
MAX_CHUNK_SIZE = 8 * 1024 * 1024
"""

INJECTED_MODEL_DEFAULT = """
import os

QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
"""

INJECTED_DIM_DEFAULT = """
import os

EMBEDDING_DIM = int(os.getenv("RAG_EMBED_DIM", "2560"))
"""

INJECTED_OR_CHAIN_DEFAULT = """
import os

# The exact defect: an 8B model default beside a 2560 dim default.
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")
EMBED_DIM = int(os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or "2560")
"""

INJECTED_VECTOR_SCHEMA = """
from qdrant_client.models import VectorParams

DENSE_VECTOR = {"size": 2560, "distance": "Cosine"}
params = VectorParams(size=2560, distance=Distance.COSINE)
"""

INJECTED_FP_STRING = """
# A HuggingFace cache path is not a model reference.
DEFAULT_TOKENIZER_PATH = "/root/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-4B/x"
# A dataset card is not a model reference.
CARD = "Training target: Qwen3-Embedding-4B fine-tuned"
# A --help description is not a model reference.
ap = argparse.ArgumentParser(description="Re-embed via DeepInfra Qwen3-Embedding-4B")
"""


def _write(tmp_path: Path, body: str, name: str = "mod_under_test.py") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_declaration_module_is_never_a_failure(tmp_path: Path) -> None:
    result = scan_static(roots=[_write(tmp_path, DECLARATION_MODULE)])
    assert result.failures == (), result.failures
    assert len(result.declarations) >= 2
    assert any(c == "REGISTRY_DECLARATION" for _, _, c, _ in result.declarations)


def test_model_registry_table_is_never_a_failure(tmp_path: Path) -> None:
    """The canonical registry names the retired model as a dict KEY."""
    result = scan_static(roots=[_write(tmp_path, MODEL_REGISTRY_MODULE)])
    assert result.failures == (), result.failures
    assert result.ok


def test_manifest_measurement_is_never_a_failure(tmp_path: Path) -> None:
    result = scan_static(roots=[_write(tmp_path, MANIFEST_MODULE)])
    assert result.failures == (), result.failures


def test_prose_alone_is_reported_but_not_a_failure(tmp_path: Path) -> None:
    result = scan_static(roots=[_write(tmp_path, PROSE_MODULE)])
    assert result.failures == ()
    assert result.ok, "false PROSE must not fail the gate"
    assert result.prose, "the false prose must still be REPORTED"
    assert any(is_doc for *_, is_doc in result.prose), "docstring claims reported as such"


def test_byte_arithmetic_is_not_embedding_drift(tmp_path: Path) -> None:
    """5 * 1024 * 1024 is a chunk size, not a vector width."""
    result = scan_static(roots=[_write(tmp_path, CHUNK_ARITHMETIC_MODULE)])
    assert result.failures == (), result.failures


def test_strings_that_merely_mention_a_retired_model_are_not_defaults(tmp_path: Path) -> None:
    """HF cache path, dataset card and --help text must not fail the gate."""
    result = scan_static(roots=[_write(tmp_path, INJECTED_FP_STRING)])
    assert result.failures == (), result.failures
    assert result.ok


# ── Static half: MUTATION EVIDENCE ─────────────────────────────────────────


def test_static_half_fails_when_a_4b_model_default_is_injected(tmp_path: Path) -> None:
    path = _write(tmp_path, INJECTED_MODEL_DEFAULT)
    result = scan_static(roots=[path])
    assert not result.ok
    assert result.failures, "a 4B model default MUST fail"
    assert "qwen3-embedding-4b" in result.failures[0][3]
    assert result.failures[0][2] == "EXECUTABLE_DEFAULT"


def test_static_half_fails_when_a_2560_string_default_is_injected(tmp_path: Path) -> None:
    path = _write(tmp_path, INJECTED_DIM_DEFAULT)
    result = scan_static(roots=[path])
    assert not result.ok
    assert result.failures
    assert "2560" in result.failures[0][3]


def test_static_half_fails_on_the_model_dim_split_or_chain(tmp_path: Path) -> None:
    """The operator's exact bug: 8B model beside a 2560 dim, via an `or` chain.

    An int-only scan misses this because the literal is a ``str``; a scan that
    ignores the ``or`` chain misses it because the literal is not a getenv
    argument. This test is the proof that neither shortcut was taken.
    """
    path = _write(tmp_path, INJECTED_OR_CHAIN_DEFAULT)
    result = scan_static(roots=[path])
    assert not result.ok
    assert any("2560" in detail for _, _, _, detail in result.failures)
    assert not any("8b" in detail.lower() for _, _, _, detail in result.failures), (
        "the canonical 8b model default must NOT be flagged"
    )


def test_static_half_fails_on_a_hardcoded_vector_schema(tmp_path: Path) -> None:
    path = _write(tmp_path, INJECTED_VECTOR_SCHEMA)
    result = scan_static(roots=[path])
    assert not result.ok
    assert len(result.failures) == 2, result.failures


def test_static_half_passes_again_after_removing_the_injection(tmp_path: Path) -> None:
    """MUTATION reversed, in the same directory, to prove the scan is live."""
    clean = _write(tmp_path, "X = 1\n", "clean.py")
    assert scan_static(roots=[clean]).ok, "precondition"

    injected = _write(tmp_path, INJECTED_MODEL_DEFAULT, "injected.py")
    assert not scan_static(roots=[injected]).ok

    injected.unlink()

    assert scan_static(roots=[clean]).ok, "post-removal must be green again"


def test_injected_int_default_is_caught(tmp_path: Path) -> None:
    path = _write(tmp_path, "EMBEDDING_DIM = 2560\n", "int_default.py")
    result = scan_static(roots=[path])
    assert not result.ok
    assert "2560" in result.failures[0][3]


def test_injected_int_default_in_a_manifest_field_is_not_a_failure(tmp_path: Path) -> None:
    """A `retired=(2560,)` record must not become a failure."""
    path = _write(tmp_path, "entry = _d(name='x', observed=2560, retired=(2560,))\n", "record.py")
    assert scan_static(roots=[path]).ok


# ── The gate against its OWN source and the real registry ──────────────────


def test_gate_does_not_flag_its_own_source() -> None:
    """dim_drift.py names the retired generation in prose and code."""
    result = scan_static(roots=[Path("src/rugbrain/dim_drift.py")])
    assert result.failures == (), result.failures
    assert result.ok


def test_real_registry_is_clean() -> None:
    """dim_policy + the whole lane package must not self-report."""
    base = Path("src/rugbrain")
    result = scan_static(roots=[base / "dim_policy.py", base / "qdrant_store"])
    assert result.failures == (), result.failures
    assert result.ok


def test_corpus_manifest_is_clean() -> None:
    result = scan_static(roots=[Path("src/rugbrain/corpus")])
    assert result.failures == (), result.failures


# ── Combined verdict ───────────────────────────────────────────────────────


def test_verify_requires_both_halves(tmp_path: Path) -> None:
    clean = _write(tmp_path, "X = 1\n", "clean.py")
    verification = verify(roots=[clean], collections=[])
    assert verification.live.checked == 0
    assert verification.static.ok
    assert verification.ok


def test_json_payload_serialises() -> None:
    payload = Verification(live=scan_live("http://127.0.0.1:1"), static=StaticCheck()).to_dict()
    encoded = json.dumps(payload)
    assert json.loads(encoded)["canonical_dim"] == CANONICAL_DIM
    assert "retired_dims" in json.loads(encoded)
