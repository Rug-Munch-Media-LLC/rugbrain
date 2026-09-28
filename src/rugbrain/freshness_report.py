"""RugBrain — the freshness vocabulary: verdicts, reasons, and the record.

One dataclass and four enums, kept apart from the probe so that what we SAY
about a collection is reviewable without reading how we MEASURE it. The
vocabulary is a set of claims; claims deserve to be argued with separately
from the reading that fills them in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

#: Boundary FRESH|AGING. From the measured age distribution (2026-09-28): the
#: 15 measured collections split 0-4d (alive) and 17d+ (dead) with an EMPTY gap
#: between 4d and 17d, so any boundary in (4, 17) is equally correct and 7 sits
#: in the middle of that gap. That empty gap is what makes ONE threshold safe
#: across every collection instead of per-feed tuning.
FRESH_MAX_DAYS: Final = 7
#: Boundary AGING|STALE. Same gap, upper end: a feed last fed 10 days ago is
#: aging, not yet dead.
AGING_MAX_DAYS: Final = 14


class FreshnessVerdict(StrEnum):
    """Four-way split. A collection is always exactly one of these."""

    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    UNMEASURABLE = "unmeasurable"


class Measurement(StrEnum):
    """Whether an age was actually read — kept separate from the age verdict.

    A collection can be MEASURED-and-stale (a dead feed we can see) or
    UNMEASURABLE (blind). Collapsing the two is how a gap becomes a silent
    omission: only the first is an alert, and only the second is a finding.
    """

    MEASURED = "measured"
    UNMEASURABLE = "unmeasurable"


class UnmeasurableReason(StrEnum):
    """Why a collection cannot state its own age. Every gap needs a reason."""

    NO_INGEST_FIELD = "no_ingest_field"
    OFF_QDRANT = "off_qdrant"
    EMPTY = "empty"
    PROBE_ERROR = "probe_error"


class OffQdrantStore(StrEnum):
    """Where a collection is actually served from when Qdrant is not its home."""

    NEO4J = "neo4j"
    CLICKHOUSE = "clickhouse"


#: Off-Qdrant collections and the home declared in ``corpus/entries_*.py``.
#: A Qdrant-side gap for one of these is NOT staleness: reporting it as stale
#: would be a false alarm on a corpus that is fed from a store this probe never
#: reads. Each is reported ``off_qdrant`` with its declared home instead.
OFF_QDRANT_HOMES: Final[dict[str, OffQdrantStore]] = {
    "wallet_profiles": OffQdrantStore.NEO4J,
    "wallet_profiles_8b": OffQdrantStore.NEO4J,
    "wallet_clusters": OffQdrantStore.NEO4J,
    "wallet_clusters_8b": OffQdrantStore.NEO4J,
    "wallet_v2": OffQdrantStore.NEO4J,
    "wallet_v2_8b": OffQdrantStore.NEO4J,
    "scanner_memory": OffQdrantStore.NEO4J,
}


@dataclass(frozen=True, slots=True)
class Probe:
    """One collection's freshness verdict and the evidence behind it."""

    collection: str
    points: int
    verdict: FreshnessVerdict
    measurement: Measurement
    reason: str
    newest: datetime | None = None
    field: str | None = None
    scanned: int = 0
    dated: int = 0
    store: OffQdrantStore | None = None

    @property
    def age_days(self) -> int | None:
        """Age in whole days, or None when nothing was measured."""
        if self.newest is None:
            return None
        return (datetime.now(UTC) - self.newest).days

    def as_dict(self) -> dict:
        """JSON-safe row. Timestamps are ISO strings, never epochs."""
        return {
            "collection": self.collection,
            "points": self.points,
            "measurement": self.measurement.value,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "newest_ingest": self.newest.isoformat() if self.newest else None,
            "field": self.field,
            "age_days": self.age_days,
            "scanned": self.scanned,
            "dated": self.dated,
            "store": self.store.value if self.store else None,
        }


def verdict_for(newest: datetime | None, now: datetime | None = None) -> FreshnessVerdict:
    """Four-way split from a measured timestamp. None means UNMEASURABLE."""
    if newest is None:
        return FreshnessVerdict.UNMEASURABLE
    from datetime import timedelta  # local: keeps the public import surface small

    age = (now or datetime.now(UTC)) - newest
    if age <= timedelta(days=FRESH_MAX_DAYS):
        return FreshnessVerdict.FRESH
    if age <= timedelta(days=AGING_MAX_DAYS):
        return FreshnessVerdict.AGING
    return FreshnessVerdict.STALE


def summarize(probes) -> dict:
    """Four-way split with point totals, so blind MASS is visible as a number.

    Counting collections alone understates the problem: 53 blind collections
    hold 81.9% of the points. The report must lead with mass, not with a count
    that makes 53 of 96 sound survivable.
    """
    rows = list(probes)
    blind = [r for r in rows if r.measurement is Measurement.UNMEASURABLE]
    total_points = sum(r.points for r in rows)
    blind_points = sum(r.points for r in blind)
    return {
        "total": len(rows),
        "total_points": total_points,
        "measured": len(rows) - len(blind),
        "unmeasurable": len(blind),
        "blind_points": blind_points,
        "blind_share": (blind_points / total_points) if total_points else 0.0,
        "by_verdict": {v.value: len([r for r in rows if r.verdict is v]) for v in FreshnessVerdict},
        "by_reason": {
            reason.value: len([r for r in blind if r.reason == reason.value])
            for reason in UnmeasurableReason
        },
    }
