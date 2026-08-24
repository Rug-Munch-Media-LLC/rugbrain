"""Dimension policy — the single source of truth for vector dims.

All Qdrant collections run ``COLLECTION_DIM`` (1024). The Qwen3 models
support Matryoshka Representation Learning: the first N dims of a larger
embedding remain a valid smaller space after renormalization.

Policy (mirrors the fleet-wide decision from the 2026-08-24 incident):
* larger than target  → MRL truncate + L2 renormalize  (never reject —
  provider output dims may change; retrieval must not break)
* exactly target      → L2 renormalize passthrough
* smaller than target → REJECT (missing dims cannot be fabricated)

This module is the ONLY place that implements the policy. Embedders
apply it at the provider boundary (``qwen_embedders``); the final
pre-Qdrant gate (``embeddings._resize``) keeps its stricter
reject-any-mismatch contract because a vector reaching it with the
wrong dim means an upstream bug, not a provider quirk.
"""

from __future__ import annotations

import math
from typing import Final

COLLECTION_DIM: Final = 1024


def normalize_dims(
    vector: list[float],
    target: int = COLLECTION_DIM,
) -> list[float]:
    """Normalize *vector* to exactly ``target`` dims.

    Larger inputs are MRL-truncated then L2-renormalized. Exact-size
    inputs are L2-renormalized (idempotent for already-normalized
    vectors). Smaller inputs raise — missing dimensions cannot be
    fabricated.
    """
    if len(vector) < target:
        raise ValueError(f"vector dim {len(vector)} < required {target}")
    cut = vector[:target]
    norm = math.sqrt(sum(x * x for x in cut))
    if norm == 0:
        return list(cut)
    return [x / norm for x in cut]


def is_valid_for_collection(vector: list[float], target: int = COLLECTION_DIM) -> bool:
    """True when the vector can be normalized to the collection dim."""
    return len(vector) >= target
