"""Dimension-drift gate: does the retired 4096-lane generation come back?

The operator decision (2026-09-29) is that qwen3-embedding-8b @ 4096 is the
only lane. 2560 (qwen3-embedding-4b) and 1024 (qwen3-embedding-0.6b) are
eliminated, not merely ignored. "Eliminated" has to mean *mechanically
enforceable*, because this project has already been bitten four separate ways:

  1. A 2560 default sat in ``embed_lane.py`` next to an already-8B model name,
     so the model said 8B and the dim said 2560. Whichever path read first won.
  2. DeepInfra's default embed model still answers **2560** with an HTTP 200
     (verified live 2026-09-27: Qwen3-Embedding-4B, 0.63s, dim 2560). A client
     that never states a dimension looks *perfectly healthy* and is wrong.
  3. A docstring asserted "every live collection is 2560-dim", which was false
     for 48 collections. The lie outlived the fact.
  4. An earlier version of *this* check read only one of Qdrant's two
     ``params.vectors`` shapes and reported healthy, populated 4096
     collections as having "no vector config".

So this module has two halves and they are deliberately independent:

  * :func:`scan_live` — GET-only probe of Qdrant. Every collection must be at
    the registry's canonical dim. No allowlist, no "expected" exceptions: a
    1024-dim collection is a bug, not a special case.
  * :func:`scan_static` — AST walk of the source tree for *executable*
    references to the retired generation.

THE CLASSIFICATION IS THE WHOLE PROBLEM
---------------------------------------
Over 100 files in this tree mention 2560. Almost all are prose describing a
generation that was retired. A check that flags those fires forever on
correct code and gets muted, which is worse than no check. A check that
ignores every ``2560`` mention misses a live default that will silently
produce wrong-width vectors. Both failure modes are real here, so every hit
is sorted into exactly one class, and the class is *derived from the
registry* rather than from a list of filenames:

  ``REGISTRY_DECLARATION``
      The retired value is being *named on purpose* so it can be recognised
      and refused. ``lane_table.PRESERVED_LANE_DIM``,
      ``lane_resolver.RETIRED_LANE_DIM``, ``dim_policy.MODEL_DIMS`` and the
      corpus manifest's ``observed``/``retired`` fields exist for no other
      reason. Deleting them would delete the ability to detect drift. Never a
      failure.

  ``MANIFEST_RECORD``
      A retired width recorded as a *measurement* — the corpus manifest's
      ``dim``/``observed``/``retired`` fields. Truthful bookkeeping, not a
      default. Never a failure.

  ``EXECUTABLE_DEFAULT``
      A retired model or width that a runtime path would actually send or fall
      back to: a ``os.getenv`` fallback a client transmits, a dim table entry,
      a vector-schema ``{"size": ...}``. **This is the failure.** It is the
      shape that produced the ``qdrant_insert_blocked requested_dim=2560
      configured_dims=[4096]`` incident and the DeepInfra 200-OK trap.

  ``MISLEADING_PROSE``
      A docstring or comment asserting the retired generation is current —
      "canonical", "native", "default", "the live corpus". Not a failure (a
      stale comment breaks nothing at runtime) but reported separately,
      because false PROSE is a slow poison: it misleads the next reader into
      reinstating exactly the default this gate forbids. Docstring hits are
      marked as such — a docstring that asserts something untrue is worse
      than a comment that is merely out of date, because a docstring is
      contract.

Nothing here is keyed to a filename. A new manifest module is covered by the
field-name rule; a new 4B default inside an existing manifest module is still
an ``EXECUTABLE_DEFAULT`` and still fails.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from rugbrain.dim_drift_live import _get_json as _get_json
from rugbrain.dim_drift_live import _shape_of as _shape_of
from rugbrain.dim_drift_live import scan_live as scan_live
from rugbrain.dim_drift_registry import (
    _REQUEST_TIMEOUT,
    CANONICAL_DIM,
    DEFAULT_QDRANT_URL,
    canonical_model,
    default_qdrant_url,
    retired_dims,
    retired_models,
)
from rugbrain.dim_drift_scan import scan_static
from rugbrain.dim_drift_types import LiveCheck as LiveCheck
from rugbrain.dim_drift_types import StaticCheck as StaticCheck
from rugbrain.dim_drift_types import Verification as Verification

__all__ = [
    "CANONICAL_DIM",
    "DEFAULT_QDRANT_URL",
    "LiveCheck",
    "StaticCheck",
    "Verification",
    "canonical_model",
    "default_qdrant_url",
    "retired_dims",
    "retired_models",
    "scan_live",
    "scan_static",
    "verify",
]


def verify(
    *,
    qdrant_url: str | None = None,
    roots: Sequence[Path | str] | None = None,
    skip: Sequence[Path | str] = (),
    timeout: float = _REQUEST_TIMEOUT,
    collections: Sequence[str] | None = None,
) -> Verification:
    """Run both halves. ``ok`` is true only when both are clean."""
    return Verification(
        live=scan_live(qdrant_url, timeout=timeout, collections=collections),
        static=scan_static(roots, skip=skip),
    )
