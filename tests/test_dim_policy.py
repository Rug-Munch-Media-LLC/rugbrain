"""Dim policy contracts — single source of truth for vector dims."""

from __future__ import annotations

import pytest

from rugbrain.dim_policy import (
    COLLECTION_DIM,
    is_valid_for_collection,
    normalize_dims,
)


def test_collection_dim_is_canonical_qwen8b():
    """Canonical write dim: qwen3-embedding-8b native (4096), local == hosted.

    Renamed from test_collection_dim_is_canonical_qwen4b. The 2560 assertion
    here was the last thing actively defending the retired 4B lane: it failed
    the moment COLLECTION_DIM was corrected, which is exactly what a test is
    for, but it means the old default had a test guarding it.
    """
    assert COLLECTION_DIM == 4096


def test_exact_passthrough_renormalized():
    v = [1.0] + [0.0] * (COLLECTION_DIM - 1)
    assert normalize_dims(v) == v


def test_oversized_refuses_rather_than_truncating():
    """A 4096 vector must not be silently cut to 2560.

    Downscaling was what made writing 8B vectors into the retired lane look
    like a supported operation. It now raises, naming both sides.
    """
    with pytest.raises(ValueError, match="refusing to truncate"):
        normalize_dims([0.25] * 4096, 2560)


def test_zero_vector_returns_zeros():
    """An exact-size zero vector is returned unchanged rather than normalised."""
    assert normalize_dims([0.0] * COLLECTION_DIM) == [0.0] * COLLECTION_DIM


def test_undersized_rejected():
    with pytest.raises(ValueError, match="required"):
        normalize_dims([0.1] * 384)


def test_custom_target_must_match_exactly():
    """An explicit target only works when it equals the vector length."""
    assert normalize_dims([3.0, 4.0], 2) == [0.6, 0.8]
    with pytest.raises(ValueError, match="refusing to truncate"):
        normalize_dims([0.25] * 512, 256)



def test_validity_check():
    assert is_valid_for_collection([0.1] * COLLECTION_DIM) is True
    assert is_valid_for_collection([0.1] * 384) is False
