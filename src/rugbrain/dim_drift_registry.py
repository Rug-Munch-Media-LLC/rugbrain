"""Registry + classification vocabulary for the dimension-drift gate.

Split out of dim_drift.py: the one place that reads the lane registry, plus the
token vocabularies that decide what a retired width *means*. This module names
the retired generation on purpose so the detector can refuse it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

from rugbrain.dim_policy import MODEL_DIMS, RETIRED_MODELS
from rugbrain.qdrant_store.lane_resolver import CANONICAL_LANE_DIM, RETIRED_LANE_DIM
from rugbrain.qdrant_store.lane_table import PINNED_DIMS, PRESERVED_LANE_DIM

#: The one dim every collection must be at. Read from the registry — never
#: hardcoded — so a future generation flip moves this gate with it.
CANONICAL_DIM: Final[int] = int(CANONICAL_LANE_DIM)

#: Qdrant is production. This module issues GET and nothing else; there is no
#: delete path, no create path, and no write verb anywhere in this file.
_READ_ONLY: Final[str] = "GET"

DEFAULT_QDRANT_URL: Final[str] = "http://127.0.0.1:6334"
_REQUEST_TIMEOUT: Final[float] = 10.0

# ── Classification vocabularies ────────────────────────────────────────────
# These name the *kind of slot* a value sits in, never a particular file. They
# are deliberately narrow: widening one is how a check starts firing forever.

#: Assignment targets / module constants whose job is to hold an EMBEDDING
#: width. Deliberately narrow: "size" alone is excluded, because this tree has
#: ``5 * 1024 * 1024`` byte arithmetic in the backup paths, and
#: a chunk size is not a vector width. A bare ``size=`` on VectorParams is
#: caught instead by the vector-construction rule below, which is precise.
_DIM_NAME_TOKENS: Final[tuple[str, ...]] = (
    "dim",
    "dimension",
    "width",
    "vector",
    "embedding",
    "embed",
    "sparse",
    "dense",
)

#: Names that exist to *name the retired generation* on purpose. A retired
#: value bound to one of these is a registry declaration, by definition.
_REGISTRY_NAME_TOKENS: Final[tuple[str, ...]] = (
    "retired",
    "preserved",
    "legacy",
    "deprecated",
    "obsolete",
    "old_dim",
    "previous",
)

#: Manifest fields that record a *measurement* rather than choose a default.
_MANIFEST_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "dim",
        "observed",
        "observed_dim",
        "observed_live_dim",
        "retired",
        "retired_dims",
        "retired_dim",
        "expected_dim",
        "native_dim",
    }
)

#: Prose words that turn a retired mention into a claim about the CURRENT lane.
_STALE_PROSE_TOKENS: Final[tuple[str, ...]] = (
    "canonical",
    "current",
    "default",
    "native",
    "live corpus",
    "every live",
    "is the dim",
    "full-fidelity",
)


def default_qdrant_url() -> str:
    """Qdrant URL, env-overridable, defaulting to the local production port."""
    return os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).rstrip("/")


def canonical_model() -> str:
    """The registry's canonical embedding model name.

    Derived, not hardcoded: the canonical model is the one whose native dim
    *is* the canonical dim. This is why the check compares against the
    registry instead of against a literal copied out of it — a future flip
    changes the answer here and every comparison follows.
    """
    for model, dim in MODEL_DIMS.items():
        if dim == CANONICAL_DIM:
            return model
    return ""


def retired_dims() -> frozenset[int]:
    """Widths the registry itself has already marked as a dead generation.

    Derived from the registry, so eliminating a generation from the registry
    eliminates it from this gate. Three sources, all of them declarations that
    exist to *name* the dead lane:

      * ``lane_resolver.RETIRED_LANE_DIM``  — the retired canonical width
      * ``lane_table.PRESERVED_LANE_DIM``   — the retained read-only base
      * ``lane_table.PINNED_DIMS.values()`` — collections pinned below canonical

    2048 (the nemotron backups) is deliberately NOT in here. Those are live
    alternative providers, not a dead generation, and treating them as drift
    would be wrong.

    The canonical dim is subtracted: on a future host where the preserved base
    has been migrated up, a preserved width equal to canonical is not drift.
    """
    candidates = {
        int(RETIRED_LANE_DIM),
        int(PRESERVED_LANE_DIM),
        *(int(d) for d in PINNED_DIMS.values()),
    }
    return frozenset(d for d in candidates if d != CANONICAL_DIM)


def retired_models() -> Mapping[str, int]:
    """Retired embedding models and the width each one produced.

    A retired model name in a *default* position is the DeepInfra trap: the
    provider answers 200 and the vectors are the wrong width.

    Reads RETIRED_MODELS, not MODEL_DIMS. The 4B was moved out of MODEL_DIMS on
    2026-09-30 so callers could not resolve its dimension and recreate the
    retired lane; deriving the retired set from MODEL_DIMS then returned {} and
    this detector would have gone blind to exactly the case it exists to catch.
    """
    return {name: dim for name, (dim, _replacement) in RETIRED_MODELS.items()}
