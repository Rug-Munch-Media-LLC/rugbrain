"""Pick the write lane whose dim matches the vector IN HAND, or fail loud.

THE BUG THIS FIXES (production, 2026-09-28)
    ``lane_resolver`` rewrites ``threat_intel_v2`` -> ``threat_intel_v2_8b``
    unconditionally, because the ``_8b`` twins are the declared write target.
    The live ``threat_intel_v2_8b`` is 4096-wide. The running service, however,
    ships ``RAG_EMBED_DIM=2560`` / ``RAG_EMBED_MODEL=qwen3-embedding-4b`` /
    ``DEEPINFRA_EMBED_MODEL=Qwen/Qwen3-Embedding-8B`` (4096), so ``get_embedding()``
    returns 2560. Result, every few seconds:

        qdrant_collection_incompatible collection=threat_intel_v2_8b
                                        requested_dim=2560 configured_dims=[4096]
        qdrant_insert_blocked collection=threat_intel_v2_8b requested_dim=2560
        threat_intel_upsert_failed token=base_0x2b08...

    25+ scanner-poller writes dropped per 10 minutes. The write was not slow or
    flaky; it was refused and the refusal was a bare ``return False``.

THE RULE
    The dim of the vector being written is a FACT, observed at the call site.
    A lane is eligible only if its DECLARED dim equals that fact. If exactly one
    eligible lane exists, write there. If none does, raise. Never truncate, never
    pad, and never silently fall back to a lane the vector does not fit.

    This is the same shape ``search/dim_guard`` already applies on the READ side
    (one embedder seam, assert-then-proceed) and the same shape ``dual_tier`` was
    repaired to. A third pattern is not introduced here: a write that cannot be
    dimension-checked is the read-side bug wearing a different hat.

WHY NOT "JUST USE THE BASE COLLECTION"
    ``threat_intel_v2`` is 2560 and is genuinely the right lane for a 2560
    vector. It is retained read-only under the no-overwrite rule, which is why
    :func:`resolve_collection` refuses to route a canonical-lane base back to
    itself. :func:`write_lane_for_dim` is the declared-table-aware counterpart:
    it looks for an ELIGIBLE LANE (base or twin) and only prefers the twin.

    The measured widths themselves live in ``lane_widths`` — this module decides,
    that module states the facts.
"""

from __future__ import annotations

import logging
from typing import Final

from rugbrain.qdrant_store.lane_resolver import (
    EIGHT_B_SUFFIX,
    strip_twin_suffix,
)
from rugbrain.qdrant_store.lane_widths import declared_width

logger = logging.getLogger(__name__)


class WriteLaneDimMismatch(ValueError):
    """No declared lane can accept a vector of this width.

    Raised instead of dropping the write. The old behaviour was a bare
    ``return False`` from the store plus a log line, which is indistinguishable
    from a Qdrant outage and lost 25+ scanner findings per 10 minutes.
    """

    def __init__(self, message: str, *, requested: int, candidates: list[str]) -> None:
        self.requested = requested
        self.candidates = candidates
        super().__init__(message)


def write_lane_for_dim(
    collection: str | None,
    vector_dim: int,
) -> str:
    """The lane a *vector_dim*-wide vector may be written to for *collection*.

    Resolution order, and why:

    1. An already-canonical ``*_8b`` name whose declared dim matches is returned
       unchanged. The caller already resolved the lane; re-deciding it here would
       reintroduce the drift this module exists to stop.
    2. A declared twin (or the base itself) whose declared dim matches. The twin
       is preferred over the base because the base is retained read-only.
    3. Otherwise :class:`WriteLaneDimMismatch`.

    A base name with no eligible lane is the common production case: the vector
    is 2560 (a 4B-generation embedder) and the only twin is 4096. The honest
    answer is to raise, because the operator must either point the env at the
    8B model or re-enable the base write; guessing is how the data was lost.
    """
    if not collection:
        raise WriteLaneDimMismatch(
            f"write_lane_unresolved requested_dim={vector_dim} reason=empty_collection",
            requested=vector_dim,
            candidates=[],
        )

    base = strip_twin_suffix(collection) or collection
    twin = f"{base}{EIGHT_B_SUFFIX}"

    if collection.endswith(EIGHT_B_SUFFIX) and declared_width(collection) == vector_dim:
        return collection

    for candidate in (twin, base):
        if declared_width(candidate) == vector_dim:
            logger.info(
                "qdrant_write_lane_resolve collection=%s resolved=%s dim=%d "
                "reason=vector_dim_match",
                collection,
                candidate,
                vector_dim,
            )
            return candidate

    # A collection with NO declared lane (scratch, test, or interim-suffixed
    # names) is outside this module's knowledge. Defer to the existing schema
    # check in store_write rather than raising: the guard exists to stop a
    # KNOWN lane from receiving the wrong-width vector, not to invent policy for
    # collections it has never heard of.
    if declared_width(twin) is None and declared_width(base) is None:
        logger.debug(
            "qdrant_write_lane_unknown collection=%s dim=%d action=defer_to_schema_check",
            collection,
            vector_dim,
        )
        return collection

    raise WriteLaneDimMismatch(
        f"write_lane_dim_mismatch collection={collection} requested_dim={vector_dim} "
        f"candidates={[(c, declared_width(c)) for c in (twin, base) if declared_width(c)]} "
        "— no declared lane accepts this vector. Either point RAG_EMBED_DIM/"
        "RAG_EMBED_MODEL at the 8B lane (4096) or re-enable the retired base "
        "collection as a write target. Never truncate and never drop the write.",
        requested=vector_dim,
        candidates=[twin, base],
    )


__all__: Final = [
    "WriteLaneDimMismatch",
    "write_lane_for_dim",
]
