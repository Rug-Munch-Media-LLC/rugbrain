"""Dim policy contracts — single source of truth for vector dims."""

from __future__ import annotations

import pytest

from rugbrain.dim_policy import COLLECTION_DIM, is_valid_for_collection, normalize_dims


def test_exact_passthrough_renormalized():
    v = [1.0] + [0.0] * 1023
    assert normalize_dims(v) == v


def test_oversized_truncated_and_renormalized():
    out = normalize_dims([2.0] * 2560)
    assert len(out) == COLLECTION_DIM
    assert sum(x * x for x in out) == pytest.approx(1.0, abs=1e-6)


def test_zero_vector_returns_zeros():
    out = normalize_dims([0.0] * 2048)
    assert len(out) == COLLECTION_DIM
    assert all(x == 0.0 for x in out)


def test_undersized_rejected():
    with pytest.raises(ValueError, match="required"):
        normalize_dims([0.1] * 384)


def test_custom_target():
    out = normalize_dims([3.0] * 512, target=256)
    assert len(out) == 256


def test_validity_check():
    assert is_valid_for_collection([0.1] * 1024) is True
    assert is_valid_for_collection([0.1] * 384) is False
