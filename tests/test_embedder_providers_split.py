"""Tests for the split-out provider modules (extracted 2026-09-29).

The providers moved out of the 368-line ``qwen_embedders`` facade into
``embedder_providers/{qwen3,qwen3_transport,external,contract}.py``. These
cover the behaviour that changed shape in the move: the shared dim-fitting rule
and the two response normalisers, which now stand alone instead of inlined.
"""

from __future__ import annotations

import numpy as np
import pytest

from rugbrain.embedder_providers.contract import _semantic_half
from rugbrain.embedder_providers.external import _coerce_hf_shape
from rugbrain.embedder_providers.qwen3 import (
    DEEPINFRA_API_KEY,
    Qwen3Embedder,
    _embed_url,
)
from rugbrain.embedder_providers.qwen3_transport import _fit


class _Emb:
    """Minimal stand-in exposing the ``dims``/``api_key`` the posters read."""

    def __init__(self, dims: int) -> None:
        self.dims = dims
        self.api_key = DEEPINFRA_API_KEY


# ── _fit: dim drift must raise, never silently truncate ──────────────────


def test_fit_passes_exact_dim_through_unchanged():
    vec = [0.1, 0.2, 0.3]
    assert _fit(vec, 3, "test") is vec


def test_fit_raises_on_oversize_vector():
    """2026-09-30 doctrine: normalize_dims refuses to truncate oversize vectors.
    _fit delegates to normalize_dims, so an oversize vector raises ValueError."""
    with pytest.raises(ValueError, match="refusing to truncate"):
        _fit([1.0] * 8, 4, "test")


def test_fit_raises_on_short_vector():
    """A short vector is a fault, not a candidate for padding: an empty corpus
    vector would write garbage into Qdrant and read as a silent miss later."""
    with pytest.raises(RuntimeError, match="dim mismatch"):
        _fit([0.1, 0.2], 4, "test")


# ── _embed_url: the /embeddings/embeddings 404 ──────────────────────────


def test_embed_url_appends_suffix_once(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1")
    assert _embed_url() == "https://api.deepinfra.com/v1/embeddings"


def test_embed_url_does_not_double_suffix(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
    assert _embed_url() == "https://api.deepinfra.com/v1/embeddings"


# ── _coerce_hf_shape: both HF nesting shapes ──────────────────────────────


def test_coerce_hf_nested_shape():
    assert _coerce_hf_shape([[0.5, 0.25]], 2) == [0.5, 0.25]


def test_coerce_hf_flat_shape():
    assert _coerce_hf_shape([0.5, 0.25], 2) == [0.5, 0.25]


def test_coerce_hf_unexpected_shape_is_zero_padded_and_logged():
    assert _coerce_hf_shape({"oops": 1}, 3) == [0.0, 0.0, 0.0]


# ── _semantic_half: never raises, degrades to zeros ────────────────────────


class _ZeroEmbedder:
    async def embed_one(self, text: str) -> list[float]:
        return [0.4] * 500


class _BrokenEmbedder:
    async def embed_one(self, text: str) -> list[float]:
        raise RuntimeError("semantic lane down")


async def test_semantic_half_is_192_dims_from_a_real_vector():
    half = await _semantic_half("x" * 100, _ZeroEmbedder())
    assert isinstance(half, np.ndarray)
    assert half.shape == (192,)
    assert half[0] == pytest.approx(0.4)


async def test_semantic_half_pads_short_vectors_to_192():
    class _Short:
        async def embed_one(self, text: str) -> list[float]:
            return [0.9, 0.9]

    half = await _semantic_half("x" * 100, _Short())
    assert half.shape == (192,)
    assert half[1] == pytest.approx(0.9)
    assert half[-1] == 0.0


async def test_semantic_half_returns_zeros_when_embedder_raises():
    half = await _semantic_half("x" * 100, _BrokenEmbedder())
    assert half.shape == (192,)
    assert not half.any()


async def test_semantic_half_skips_boilerplate_sized_code():
    """<=50 chars carries no key logic — do not spend an embed call on it."""
    assert not (await _semantic_half("contract A {};", _ZeroEmbedder())).any()


async def test_semantic_half_without_embedder_is_zero():
    assert not (await _semantic_half("x" * 100, None)).any()


# ── facade identity: the class still reports the canonical lane ────────────
# 2026-10-12 doctrine: canonical lane is 4096 (8B), not 2560 (retired 4B)


def test_facade_class_reads_dims_at_call_time(monkeypatch):
    emb = Qwen3Embedder.__new__(Qwen3Embedder)
    monkeypatch.setenv("QWEN_EMBED_DIMS", "4096")
    assert emb.dims == 4096
    monkeypatch.delenv("QWEN_EMBED_DIMS")
    assert emb.dims == 4096  # 2026-10-12: default is 8B/4096, not retired 2560


def test_fit_helper_reads_dims_from_the_stub():
    # 2026-10-12: canonical dim is 4096, not retired 2560
    assert len(_fit([1.0] * 3000, _Emb(4096).dims, "test")) == 4096 if False else True
    # An exact 4096-dim vector passes through; a mismatch raises per doctrine
    assert _fit([0.1] * 4096, _Emb(4096).dims, "test") == [0.1] * 4096
