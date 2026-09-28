"""Golden-row verification state machine.

Verification is a HUMAN judgement. This module makes it *well-defined* and
*auditable* without performing it.

Why this exists
---------------
``tests/test_eval_harness.py`` asserted that every golden row was
``provenance="verified"`` against "the 2026-09-19 hardening pass". That claim
was false: the shipped set carried no ``provenance`` field at all, so
``verified`` was the empty set and the assertion could only ever fail. The
tempting fix — relabel rows ``verified`` — is forbidden: it manufactures the
judgement the field is supposed to record.

So verification is modelled as an explicit state machine backed by an
EVIDENCE sidecar. A row is ``verified`` only when an evidence record exists
that names the corpus it was checked against, the ids actually observed, and
who confirmed it. Absent that, the row is ``unverified`` and appears in a
review worklist. Nothing here decides anything; it only records and audits.

States
------
``unverified``  no evidence on file — needs a human
``pending``     a human has been assigned, no decision yet
``verified``    evidence on file: ids observed in a live query result
``rejected``    a human judged the row's ground truth to be wrong

Transitions are only ever *recorded*, never inferred.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# Legal provenance values, mirrored in tests. ``synthetic`` is retained for
# rows that are deliberately not corpus-anchored (no gold at all).
VERIFIED = "verified"
SYNTHETIC = "synthetic"
LEGAL_STATES = frozenset({VERIFIED, SYNTHETIC, "unverified", "pending", "rejected"})

# Evidence sidecar, next to the golden set it describes.
EVIDENCE_FILENAME = "verification.json"
EVIDENCE_SCHEMA = 1


class Decision(StrEnum):
    """The four states a golden row can occupy."""

    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Evidence:
    """One human verification record for a single golden row.

    ``observed_ids`` are the point ids a human actually saw for this query.
    A record with an empty ``observed_ids`` is NOT sufficient to verify — the
    test ``test_verified_rows_have_stable_ids`` exists precisely because a
    ``verified`` row without ids cannot be scored, so it can never have been
    really checked.
    """

    row_id: str
    collection: str
    observed_ids: list[str] = field(default_factory=list)
    reviewer: str = ""
    confirmed_at: str = ""
    note: str = ""

    @property
    def is_sufficient(self) -> bool:
        """True when this record can actually support a ``verified`` claim."""
        return bool(self.observed_ids) and bool(self.reviewer) and bool(self.confirmed_at)


def row_id(query: str) -> str:
    """Deterministic content-addressed id for a golden row.

    Derived from the query text alone so it is stable across reorders and
    file rewrites, and so two reviewers independently computing it agree.
    """
    return "g-" + hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:12]


def load_evidence(path: Path | str) -> dict[str, Evidence]:
    """Read the evidence sidecar. A missing file means "nothing verified yet".

    This is the honest default: an absent sidecar is zero verified rows, which
    is a true statement about the corpus, not a failure to be papered over.
    """
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError(
            f"evidence sidecar schema mismatch in {path}: "
            f"expected {EVIDENCE_SCHEMA}, got {payload.get('schema')!r}"
        )
    out: dict[str, Evidence] = {}
    for entry in payload.get("records", []):
        rec = Evidence(
            row_id=entry["row_id"],
            collection=entry.get("collection", ""),
            observed_ids=list(entry.get("observed_ids", [])),
            reviewer=entry.get("reviewer", ""),
            confirmed_at=entry.get("confirmed_at", ""),
            note=entry.get("note", ""),
        )
        if rec.row_id in out:
            # A duplicate id means the sidecar is ambiguous about which
            # judgement applies. Refuse rather than pick one silently.
            raise ValueError(f"duplicate evidence record for row {rec.row_id} in {path}")
        out[rec.row_id] = rec
    return out


def decide(record: Evidence | None) -> Decision:
    """Resolve a row's state from its evidence record alone.

    No evidence, or evidence too thin to support the claim, is UNVERIFIED.
    That is the whole point: the state is a function of the audit trail, so
    it cannot be inflated by editing the data.
    """
    if record is None:
        return Decision.UNVERIFIED
    if not record.is_sufficient:
        return Decision.UNVERIFIED
    return Decision.VERIFIED


def counts(queries: list[str], evidence: dict[str, Evidence]) -> dict[str, int]:
    """Tally rows per decision. ``needs_human`` is the number that must be read."""
    tally = {d.value: 0 for d in Decision}
    for q in queries:
        tally[decide(evidence.get(row_id(q))).value] += 1
    tally["total"] = len(queries)
    tally["needs_human"] = sum(
        1 for q in queries if decide(evidence.get(row_id(q))) is Decision.UNVERIFIED
    )
    return tally


def worklist(queries: list[str], evidence: dict[str, Evidence], limit: int = 20) -> list[dict]:
    """Rows still needing a human, with the evidence each one is missing.

    Bounded by *limit* because this feeds a reviewer's queue. The bound is a
    display cap on a work queue, never a cap on what the gate measures.
    """
    out: list[dict] = []
    for q in queries:
        rid = row_id(q)
        if decide(evidence.get(rid)) is not Decision.UNVERIFIED:
            continue
        rec = evidence.get(rid)
        missing = []
        if rec is None:
            missing.append("no evidence record")
        else:
            if not rec.observed_ids:
                missing.append("observed_ids")
            if not rec.reviewer:
                missing.append("reviewer")
            if not rec.confirmed_at:
                missing.append("confirmed_at")
        out.append({"row_id": rid, "query": q, "missing": missing})
        if len(out) >= limit:
            break
    return out


__all__ = [
    "Decision",
    "EVIDENCE_FILENAME",
    "EVIDENCE_SCHEMA",
    "Evidence",
    "LEGAL_STATES",
    "SYNTHETIC",
    "VERIFIED",
    "counts",
    "decide",
    "load_evidence",
    "row_id",
    "worklist",
]
