"""The lane policy, asserted as data — one test, one pair per inversion.

Every defect this file guards was a PORT that served a different model than the
code claimed, so each assertion compares a port to the DIMENSION it must serve,
never to another comment.

MEASURED 2026-10-30 from ``/proc/<pid>/cmdline`` and ``/v1/models``:
    :8004  Qwen3-Embedding-8B-Q8_0   4096  no bearer  CORPUS
    :8024  Qwen3-Embedding-0.6B-Q8_0 1024  bearer     MEMORY

One public function on purpose: a policy gate is one assertion over several
facts, and the repo's new-code rule is one primary function per module.
"""

from __future__ import annotations

import importlib

import pytest

from rugbrain import embeddings
from rugbrain.embedder_providers import local as local_provider
from rugbrain.embeddings_pkg import config as lane_config
from rugbrain.embeddings_pkg.width_guard import accepts_vector
from rugbrain.rerank_lanes import DEFAULT_ORDER

CORPUS_PORT = 8004
MEMORY_PORT = 8024

# Hermetic: no client is ever built here. Declared explicitly because the AST
# marker guess cannot tell an import from a dialled socket.
pytestmark = pytest.mark.unit


def _corpus_default_names_the_8b_port() -> None:
    assert f":{CORPUS_PORT}" in lane_config.QWEN_EMBED_URL
    assert f":{MEMORY_PORT}" not in lane_config.QWEN_EMBED_URL, (
        "the corpus lane must never default to the 0.6B/1024 memory lane"
    )


def _local_provider_names_the_8b_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBED_LOCAL_URL", raising=False)
    assert f":{CORPUS_PORT}" in importlib.reload(local_provider).ENDPOINT
    assert f":{MEMORY_PORT}" not in local_provider.ENDPOINT


def _chain_is_hosted_first_and_local_cold_last() -> None:
    chain = embeddings.AUTO_CHAIN
    assert chain[0] == "siliconflow", "the primary must lead"
    assert chain[-1] == "int", "the local 8B lane must be cold LAST"
    assert "deepinfra" in chain, "DeepInfra stays a fallback rung"


def _memory_width_never_enters_the_corpus() -> None:
    assert lane_config.EMBEDDING_DIM == 4096
    assert accepts_vector([0.0] * 1024, 4096, "memory-lane") is False
    assert accepts_vector([0.0] * 4096, 4096, "corpus-lane") is True


def _rerank_order_has_no_local_rung() -> None:
    """Rerank is API-only: the lane table that dispatch and search read has
    no local cross-encoder, so a total provider outage SKIPS rather than
    scoring the corpus with a model nobody pinned."""
    assert "local" not in DEFAULT_ORDER
    assert "int" not in DEFAULT_ORDER
    assert DEFAULT_ORDER[0] == "siliconflow"
    assert "deepinfra" in DEFAULT_ORDER


def test_lane_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    _corpus_default_names_the_8b_port()
    _local_provider_names_the_8b_port(monkeypatch)
    _chain_is_hosted_first_and_local_cold_last()
    _memory_width_never_enters_the_corpus()
    _rerank_order_has_no_local_rung()
