"""The MEASURED width of every write lane, pinned as a constant.

WHY THIS IS A SEPARATE MODULE
    ``lane_table.CANONICAL_LANE_DIM`` is ``dim_policy.COLLECTION_DIM``, which
    reads ``RAG_EMBED_DIM`` — the same env var the embedder reads. When the
    operator env pins 2560, the declaration silently becomes 2560 too and
    ``declared_lane_dim("threat_intel_v2_8b")`` answers 2560 for a collection
    that is physically 4096. The table was verified against live Qdrant once
    and then believed forever.

    A declaration that moves with the same env var as the vector it is supposed
    to be checked against is not a declaration. The widths here are therefore
    written down as measured constants, imported by the write path and never
    re-derived from configuration.

    Split out of ``write_lane`` (which decides) so the facts can be audited
    without reading the routing policy: a reviewer asks "is 4096 right?" and
    this file is the whole answer.
"""

from __future__ import annotations

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

#: The canonical lane dim, PINNED here and deliberately NOT read from
#: ``lane_table.CANONICAL_LANE_DIM``.
#:
#: Every ``_8b`` twin was verified against live Qdrant at 4096
#: (``http://rmi-qdrant-v2:6334``: 96 collections, 33 twins, 2026-09-28).
#: That measured fact is the whole basis for routing, so it is a constant
#: rather than a re-read of the embedder's own configuration.
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


def declared_width(name: str) -> int | None:
    """Declared dim for *name*, base or twin, or None when unclassified.

    Four disjoint groups, and conflating any two of them is the bug:

    * a ``*_8b`` twin            -> :data:`TWIN_LANE_DIM` (4096, measured live)
    * a base of a declared twin  -> ``PRESERVED_LANE_DIM`` (the retired gen)
    * a name in ``CANONICAL_BASES`` -> :data:`TWIN_LANE_DIM` (no twin, no rewrite)
    * a name in ``PINNED_DIMS``  -> its own pinned dim (1024 / 2048 / 2560)

    A twin is NOT "whatever ``CANONICAL_LANE_DIM`` currently says"; see
    :data:`TWIN_LANE_DIM`.
    """
    if name.endswith(EIGHT_B_SUFFIX) and (strip_twin_suffix(name) or "") in DECLARED_TWINS:
        return TWIN_LANE_DIM
    if name in CANONICAL_BASES:
        return TWIN_LANE_DIM
    if name in DECLARED_TWINS:
        return PRESERVED_LANE_DIM
    return PINNED_DIMS.get(name)


__all__: Final = ["CANONICAL_BASES", "TWIN_LANE_DIM", "declared_width"]
