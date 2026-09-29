"""Planning for the 2560 -> 4096 twin fill: the id diff, and the refusals.

The plan is a MEASUREMENT, never a table. Counts in a table go stale the
moment the scanner writes; this module re-reads both collections and lets the
SET DIFFERENCE of point ids decide what to embed. That is why
codebase_reference_v2 costs 4,644 and not 2,131,274 — its twin already holds
2,126,630 and re-embedding those would be pure wasted DeepInfra spend.

REVISED 2026-09-29 after a payload scan. Two corrections to the earlier plan,
both of which the tool now enforces rather than assumes:

  * ``scan_intel`` (52k) and ``agent_vectors`` (182) are EXCLUDED. Their
    payloads carry no text field — only keys, addresses and scores — so the
    text their 2560 vectors were built from is unrecoverable. Re-embedding
    them from synthesised ``key=value`` filler would write vectors that
    retrieve nothing, and they are moving to ClickHouse as address-keyed
    lookup data instead (another lane). Excluding them is what takes the job
    from 60,255 points to ~8k.
  * ``rug_events``, ``public_crypto_patterns`` and ``scanned_tokens`` have NO
    twin at all, so their 2560 base is the only copy in Qdrant and B2 does not
    back up the 2560 bases. These are the dangerous three: the tool CREATES
    the twin, fills it, and verifies parity, and still never deletes the base.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from . import qdrant_io as q
from .targets import CANONICAL_DIM, Target, twin_name


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
    # Missing ids whose payload offers no recoverable embed input (the
    # codebase docs:-library residue). Counted so a shortfall is explained
    # rather than mysterious; never embedded.
    unrecoverable: int = 0
    created_twin: bool = False
    failed: list[str] = field(default_factory=list)
    status: str = "PENDING"
    seconds: float = 0.0

    @property
    def parity(self) -> bool:
        return self.twin_after >= self.base_count

    @property
    def expected_after(self) -> int:
        """Twin count a completed run must reach: the base's ids, all of them."""
        return self.twin_before + self.to_embed


def plan(c: httpx.Client, t: Target, *, execute: bool = False) -> Report:
    """Measure both sides, enforce the dim contract, size the diff.

    Refuses rather than guesses. The operator's explicit requirement: if a
    base turns out not to be 2560, STOP and report — do not adapt.

    ``execute`` is the ONLY thing that may create a missing twin, and only for
    a target marked ``create_twin``. A dry run reports the twin as absent and
    sizes the diff as if it were empty, so the operator sees the cost before
    anything is written to production.
    """
    twin = twin_name(t.base)
    base_info = q.collection_info(c, t.base)
    if base_info is None:
        return Report(
            t.base, twin, 0, 0, status="SKIP", failed=[f"base collection {t.base} does not exist"]
        )

    b_dim, b_vname = q.vector_shape(base_info)
    if b_dim != t.planned_base_dim:
        return Report(
            t.base,
            twin,
            base_info["points_count"],
            0,
            status="REFUSED",
            failed=[f"base dim is {b_dim}, plan says {t.planned_base_dim}"],
        )

    base_count = int(base_info["points_count"])
    twin_info = q.collection_info(c, twin)
    if twin_info is None:
        if not t.create_twin:
            return Report(
                t.base,
                twin,
                base_count,
                0,
                status="REFUSED",
                failed=[f"twin {twin} does not exist and is not a create target"],
            )
        if not execute:
            return Report(
                t.base,
                twin,
                base_count,
                0,
                to_embed=base_count,
                status="CREATE-ON-EXECUTE",
                failed=[f"twin {twin} absent; --execute will create it ({base_count} points)"],
            )
        q.create_twin(c, twin, size=CANONICAL_DIM, vector_name=b_vname)
        twin_info = q.collection_info(c, twin)
        if twin_info is None:
            return Report(
                t.base,
                twin,
                base_count,
                0,
                status="FAILED",
                failed=[f"create of {twin} did not materialise; base left untouched"],
            )
        created = True
    else:
        created = False

    t_dim, _ = q.vector_shape(twin_info)
    if t_dim != CANONICAL_DIM:
        return Report(
            t.base,
            twin,
            base_count,
            int(twin_info["points_count"]),
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
        base_count,
        int(twin_info["points_count"]),
        to_embed=missing,
        created_twin=created,
        status="OK",
    )
