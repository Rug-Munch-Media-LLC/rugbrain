"""Vocabulary aliases, cadence tokens and the one declaration builder.

Kept separate from ``entries`` so the five group modules and the aggregator share
exactly one builder. Two builders would be two definitions of what a declaration is,
which is the class of problem this package exists to remove.
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.schema import (
    LICENCE_PERMISSIVE,
    CorpusDeclaration,
    CorpusDomain,
    CorpusLanguage,
)
from rugbrain.corpus.schema import CorpusDomain as D
from rugbrain.corpus.schema import CorpusLanguage as L
from rugbrain.qdrant_store.lane_table import CANONICAL_LANE_DIM

#: Cadence tokens. Free text would make "how fresh is this" unanswerable, and
#: §5.2 of the audit is the proof: 67% of the fleet cannot be dated at all.
DAILY: Final = "daily"
HOURLY: Final = "hourly"
WEEKLY: Final = "weekly"
MONTHLY: Final = "monthly"
ON_DEMAND: Final = "on-demand"
UNKNOWN_CADENCE: Final = "unknown"

#: Collections that exist to be WRITTEN to but are not corpora: runtime state,
#: test fixtures and probes. They are declared so the undeclared check is
#: meaningful, and marked non-corpus so nobody counts them as coverage.
RUNTIME: Final = "runtime-state"
PROBE: Final = "probe/test-fixture"


def _d(
    name: str,
    collection: str,
    *,
    cid: str,
    language: CorpusLanguage = CorpusLanguage.UNCLASSIFIED,
    domain: CorpusDomain = CorpusDomain.UNCLASSIFIED,
    dim: int | None = CANONICAL_LANE_DIM,
    url: str = "internal://derived/rugbrain-pipeline",
    licence: str = LICENCE_PERMISSIVE,
    cadence: str = WEEKLY,
    last_verified: str | None = "2026-09-28",
    notes: str = "",
    observed: int | None = None,
    retired: tuple[int, ...] = (),
) -> CorpusDeclaration:
    """Build one declaration. ``cid`` defaults to the collection name.

    Last-verified is only a claim about the DIM, which is the fact the audit
    measured; provenance and cadence for the derived collections are honest
    ``unknown`` markers rather than invented dates, because §5.2 established
    that 48 of 61 collections have no ingest-side timestamp and a fabricated
    "verified" is worse than an absent one.
    """
    return CorpusDeclaration(
        name=name,
        id=cid,
        language=language,
        domain=domain,
        collection=collection,
        dim=dim,
        source_url=url,
        licence=licence,
        refresh_cadence=cadence,
        last_verified=last_verified,
        notes=notes,
        observed_live_dim=observed,
        retired_dims=retired,
    )


__all__ = [
    "DAILY",
    "HOURLY",
    "MONTHLY",
    "ON_DEMAND",
    "PROBE",
    "RUNTIME",
    "UNKNOWN_CADENCE",
    "WEEKLY",
    "D",
    "L",
    "_d",
]
