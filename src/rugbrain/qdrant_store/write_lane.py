"""Pick the write lane whose dim matches the vector IN HAND, or fail loud.

THE BUG THIS FIXES (production, 2026-09-28)
    ``lane_resolver`` rewrites ``threat_intel_v2`` -> ``threat_intel_v2_8b``
    unconditionally, because the ``_8b`` twins are the declared write target.
    The live ``threat_intel_v2_8b`` is 4096-wide. The running service, however,
    ships ``RAG_EMBED_DIM=2560`` / ``RAG_EMBED_MODEL=qwen3-embedding-4b`` /
    ``DEEPINFRA_EMBED_MODEL=Qwen/Qwen3-Embedding-4B``, so ``get_embedding()``
    returns 2560. Result, every few seconds:

        qdrant_collection_incompatible collection=threat_intel_v2_8b
                                        requested_dim=2560 configured_dims=[4096]
        qdrant_insert_blocked collection=threat_intel_v2_8b requested_dim=2560
        threat_intel_upsert_failed token=base_0x2b08...

    25+ scanner-poller writes dropped per 10 minutes. The write was not slow or
    flaky; it was refused and the refusal was a bare ``return False``.

WHY THE DECLARED TABLE WAS NOT ENOUGH
    ``lane_table.CANONICAL_LANE_DIM`` is derived from ``dim_policy.COLLECTION_DIM``,
    which reads the SAME ``RAG_EMBED_DIM`` env var the embedder reads. So when
    the operator env pins 2560, the declaration silently becomes 2560 too and
    ``declared_lane_dim("threat_intel_v2_8b")`` answers 2560 for a collection
    that is physically 4096. The table was verified against live Qdrant once and
    then believed forever. A declaration that moves with the same env var as the
    vector it is supposed to be checked against is not a declaration.

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
"""

from __future__ import annotations

import logging
from typing import Final

from rugbrain.qdrant_store.lane_resolver import (
    EIGHT_B_SUFFIX,
    strip_twin_suffix,
)
from rugbrain.qdrant_store.lane_table import (
    DECLARED_TWINS,
    PINNED_DIMS,
    PRESERVED_LANE_DIM,
)

logger = logging.getLogger(__name__)

#: The canonical lane dim is PINNED here, deliberately not read from
#: ``lane_table.CANONICAL_LANE_DIM``.
#:
#: ``CANONICAL_LANE_DIM`` is ``dim_policy.COLLECTION_DIM``, which reads
#: ``RAG_EMBED_DIM`` -- the same env var the embedder reads. On the live service
#: that var is 2560, so importing it here would re-import the exact defect: the
#: declaration would follow the vector instead of checking it, and a 2560 vector
#: would be declared to fit a 4096 collection. Every ``_8b`` twin was verified
#: against live Qdrant at 4096 (96 collections, 33 twins, 2026-09-28); that
#: measured fact is the whole basis for routing, so it is written down here as a
#: constant rather than derived from configuration.
TWIN_LANE_DIM: Final[int] = 4096

#: Owned names that are ALREADY canonical at :data:`TWIN_LANE_DIM` and never had
#: a 2560 base to retire, so they have no twin and no entry in ``PINNED_DIMS``.
#: They are legitimate 4096 write targets and must not be reported as
#: unclassified. Declared here rather than in ``lane_table`` because the table
#: treats them as "nothing to do" (their whole point is that they need no
#: rewrite) while a WRITE path still has to know their width.
#: Verified live 2026-09-28 alongside the twin list.
CANONICAL_BASES: Final[frozenset[str]] = frozenset(
    {
        "bridge_architecture",
        "crime_reports",
        "l2_risk",
        "market_data",
        "newsletters",
        "podcast_episodes",
        "privacy_protocol",
        "scam_signatures",
        "standards",
        "zk_circuits",
    }
)


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


def _declared_dim(name: str) -> int | None:
    """Declared dim for *name*, base or twin, or None when unclassified.

    Three disjoint groups, and conflating any two of them is the bug:

    * a ``*_8b`` twin           -> :data:`TWIN_LANE_DIM` (4096, measured live)
    * a base of a declared twin -> :data:`PRESERVED_LANE_DIM` (the retired gen)
    * a name in ``PINNED_DIMS`` -> its own pinned dim (1024 / 2048 / 2560)
    * a name in ``CANONICAL_BASES`` -> :data:`TWIN_LANE_DIM` (no twin, no rewrite)

    A twin is NOT "whatever ``CANONICAL_LANE_DIM`` currently says"; see the
    constant's docstring.
    """
    if name.endswith(EIGHT_B_SUFFIX) and (strip_twin_suffix(name) or "") in DECLARED_TWINS:
        return TWIN_LANE_DIM
    if name in CANONICAL_BASES:
        return TWIN_LANE_DIM
    if name in DECLARED_TWINS:
        return PRESERVED_LANE_DIM
    return PINNED_DIMS.get(name)


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

    if collection.endswith(EIGHT_B_SUFFIX) and _declared_dim(collection) == vector_dim:
        return collection

    for candidate in (twin, base):
        if _declared_dim(candidate) == vector_dim:
            logger.info(
                "qdrant_write_lane_resolve collection=%s resolved=%s dim=%d "
                "reason=vector_dim_match",
                collection,
                candidate,
                vector_dim,
            )
            return candidate

    raise WriteLaneDimMismatch(
        f"write_lane_dim_mismatch collection={collection} requested_dim={vector_dim} "
        f"candidates={[(c, _declared_dim(c)) for c in (twin, base) if _declared_dim(c)]} "
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
