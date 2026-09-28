"""Dimension policy — the single source of truth for vector dims.

Current Qdrant collections run ``COLLECTION_DIM`` = 4096, the
qwen3-embedding-8b native dim, identical locally and on DeepInfra.

2026-09-28 (operator decision): the canonical lane is 8B @ 4096 and that is
now the DEFAULT here as well as in the env. This module was the actual root of
a 2560/4096 split — COLLECTION_DIM was hardcoded to 2560 with no env override,
MODEL_DIMS had no 8b entry, and the docstring claimed "every live collection is
2560-dim", which was false. The result was that paths resolving their dim from
here got 2560 and were refused by the store's dimension guard:
  qdrant_insert_blocked collection=privacy_protocol requested_dim=2560
                                         configured_dims=[4096]
The guard was right. This default was wrong.

Retired generations, kept for reading, never written to by the canonical lane:
  2560 = qwen3-embedding-4b  (retained base collections, no-overwrite rule)
  2048 = nemotron era
  1024 = qwen3-embedding-0.6b (retired 2026-09-14)
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
import os
from typing import Final

# Canonical write dim: qwen3-embedding-8b is 4096-dim natively, both locally
# (:8004) and on DeepInfra. Env-overridable so a different corpus can still be
# targeted, but the DEFAULT is the canonical lane — previously this was a
# hardcoded 2560 with no override at all, which is how the split persisted.
COLLECTION_DIM: Final = int(os.environ.get("RAG_EMBED_DIM", "4096"))

# Native output dims per embedding model (model-card truth, verified live
# 2026-08-25 against :8004/DeepInfra). Single source of truth for
# "which dim does this model produce" — modules must derive defaults from
# here instead of hardcoding stale numbers.
MODEL_DIMS: Final[dict[str, int]] = {
    # CANONICAL. Must stay in step with COLLECTION_DIM above and with the
    # defaults in src/rugbrain/embeddings.py and scripts/embed_lane.py.
    "qwen3-embedding-8b": 4096,
    # Retired generation, still readable. A 2560 base collection is retained
    # under the no-overwrite rule, so this entry is for READING it, never for
    # writing: the canonical lane must not be coerced into a 2560 space.
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
