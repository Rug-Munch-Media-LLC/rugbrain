"""Planning for the 2560 -> 4096 twin fill: the id diff, and the refusals.

The plan is a MEASUREMENT, never a table. Counts in a table go stale the
moment the scanner writes; this module re-reads both collections and lets the
SET DIFFERENCE of point ids decide what to embed. That is why
codebase_reference_v2 costs 4,644 and not 2,131,274 — its twin already holds
2,126,630 and re-embedding those would be pure wasted DeepInfra spend.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from . import qdrant_io as q

LEGACY_DIM = 2560
CANONICAL_DIM = 4096
TWIN_SUFFIX = "_8b"


@dataclass(frozen=True)
class Target:
    """One base and the twin it must fill. Deliberately carries NO counts."""

    base: str
    planned_base_dim: int
    note: str = ""


# The 12 lagging collections, measured 2026-09-29 against :6334. The names
# and their PLANNED dim are the contract; the counts are re-measured at run
# time. threat_intel_v2 and scan_intel are live scanner targets and drift
# upward continuously — which is precisely why no count is stored here.
TARGETS: tuple[Target, ...] = (
    Target("scan_intel", LEGACY_DIM, "87% of the work; no text field, reconstructed"),
    Target("codebase_reference_v2", LEGACY_DIM, "twin already ~99.8% full; only the tail"),
    Target("rug_events", LEGACY_DIM, "twin absent — needs create, not fill"),
    Target("education", LEGACY_DIM, ""),
    Target("agent_vectors", LEGACY_DIM, ""),
    Target("security_feeds", LEGACY_DIM, ""),
    Target("threat_intel_v2", LEGACY_DIM, "live scanner target; count drifts"),
    Target("security_alerts", LEGACY_DIM, ""),
    Target("research_v2", LEGACY_DIM, ""),
    Target("security_patterns_test", LEGACY_DIM, ""),
    Target("public_crypto_patterns", LEGACY_DIM, "twin absent — needs create"),
    Target("scanned_tokens", LEGACY_DIM, "twin absent — needs create"),
)


@dataclass
class Report:
    """One row per target. Always printed, whatever happened."""

    base: str
    twin: str
    base_count: int
    twin_before: int
    twin_after: int = 0
    to_embed: int = 0
    embedded: int = 0
    failed: list[str] = field(default_factory=list)
    status: str = "PENDING"
    seconds: float = 0.0

    @property
    def parity(self) -> bool:
        return self.twin_after >= self.base_count


def twin_name(base: str) -> str:
    return base + TWIN_SUFFIX


def plan(c: httpx.Client, t: Target) -> Report:
    """Measure both sides, enforce the dim contract, size the diff.

    Refuses rather than guesses. The operator's explicit requirement: if
    scan_intel turns out not to be 2560, STOP and report — do not adapt.
    """
    twin = twin_name(t.base)
    base_info = q.collection_info(c, t.base)
    if base_info is None:
        return Report(
            t.base, twin, 0, 0, status="SKIP", failed=[f"base collection {t.base} does not exist"]
        )

    b_dim, _ = q.vector_shape(base_info)
    if b_dim != t.planned_base_dim:
        return Report(
            t.base,
            twin,
            base_info["points_count"],
            0,
            status="REFUSED",
            failed=[f"base dim is {b_dim}, plan says {t.planned_base_dim}"],
        )

    twin_info = q.collection_info(c, twin)
    if twin_info is None:
        return Report(
            t.base,
            twin,
            base_info["points_count"],
            0,
            status="REFUSED",
            failed=[f"twin {twin} does not exist; creating it is a separate job"],
        )

    t_dim, _ = q.vector_shape(twin_info)
    if t_dim != CANONICAL_DIM:
        return Report(
            t.base,
            twin,
            base_info["points_count"],
            twin_info["points_count"],
            status="REFUSED",
            failed=[f"twin dim is {t_dim}, not {CANONICAL_DIM}"],
        )

    # Diff on point id. Ids are comparable because base and twin share them:
    # verified 2026-09-29 by sampling paired points across education,
    # security_feeds and codebase_reference_v2 — same id, twin holding the
    # same payload plus the four provenance keys.
    twin_ids = q.twin_id_set(c, twin)
    missing = sum(1 for page in q.scroll(c, t.base) for p in page if str(p["id"]) not in twin_ids)
    return Report(
        t.base,
        twin,
        base_info["points_count"],
        twin_info["points_count"],
        to_embed=missing,
        status="OK",
    )
