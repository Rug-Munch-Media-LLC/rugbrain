"""Dimension policy — the single source of truth for vector dims.

All current Qdrant collections run ``COLLECTION_DIM`` (2560 = the
qwen3-embedding-4b native dim, identical locally and on DeepInfra).
All legacy 1024-dim collections were retired with the 0.6b lane
(2026-09-14); every live collection is 2560-dim.
The Qwen3 models
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

# Canonical write dim for ALL current collections: qwen3-embedding-4b is
# 2560-dim natively, both locally (:8004) and on DeepInfra. (The 1024
# default survived from the retired qwen3-embedding-0.6b era and could
# silently MRL-truncate newer embeddings — fixed 2026-09-14.)
COLLECTION_DIM: Final = 2560

# Native output dims per embedding model (model-card truth, verified live
# 2026-08-25 against :8004/DeepInfra). Single source of truth for
# "which dim does this model produce" — modules must derive defaults from
# here instead of hardcoding stale numbers.
MODEL_DIMS: Final[dict[str, int]] = {
    "qwen3-embedding-4b": 2560,
    # OpenRouter :free AND NVIDIA NIM direct — same model, two quotas.
    "nvidia/nemotron-3-embed-1b": 2048,
    # NIM direct backup #2 (vision-language embed, text works fine).
    "nvidia/llama-nemotron-embed-vl-1b-v2": 2048,
}


def native_dims(model: str) -> int | None:
    """Native output dim for *model*, or None when unknown."""
    return MODEL_DIMS.get(model.strip().lower())


def default_dim_for_model(model: str, fallback: int = COLLECTION_DIM) -> int:
    """Registry-aware default dim; *fallback* when model is unknown."""
    return native_dims(model) or fallback


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
