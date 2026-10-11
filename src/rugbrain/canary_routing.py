"""Default-off, per-collection 8B canary routing.

The canonical RugBrain path remains Qwen3-Embedding-8B at 2560 dimensions.
This module only enables an explicitly configured, read-only canary for
collections that have a complete 4096-dimensional target. It does not change
ingestion, global collection configuration, or the separate 8B migration.

This file is the stable public surface. The work is split by responsibility:

  * :mod:`rugbrain.canary_config`     — allowlist, env gate, target naming
  * :mod:`rugbrain.canary_embedding`  — credential, provider call, dim check
  * :mod:`rugbrain.canary_sanitize`   — hiding the ``_8b`` shadow name

Every name below is imported *into* this module's namespace, not defined
here, so ``monkeypatch.setattr(rugbrain.canary_routing, name, ...)`` keeps
patching the same binding callers resolve.
"""

from __future__ import annotations

from rugbrain.canary_config import (
    ALLOWED_CANARY_SOURCES,
    CANARY_DIM,
    CANARY_MODEL,
    CANARY_SUFFIX,
    CanaryConfigError,
    CanaryUnavailable,
    canary_collections,
    canary_enabled,
    canary_target_for,
)
from rugbrain.canary_embedding import get_canary_query_embedding, validate_canary_vector
from rugbrain.canary_sanitize import sanitize_canary_result

__all__ = [
    "ALLOWED_CANARY_SOURCES",
    "CANARY_DIM",
    "CANARY_MODEL",
    "CANARY_SUFFIX",
    "CanaryConfigError",
    "CanaryUnavailable",
    "canary_collections",
    "canary_enabled",
    "canary_target_for",
    "get_canary_query_embedding",
    "sanitize_canary_result",
    "validate_canary_vector",
]
