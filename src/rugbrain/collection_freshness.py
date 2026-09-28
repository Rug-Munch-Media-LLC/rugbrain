"""RugBrain — per-collection ingest freshness, measured or explicitly unmeasurable.

Answers one question per collection: **when was this collection last
meaningfully ingested?** Every collection gets a verdict, and the verdict is
either MEASURED (backed by a field an ingester stamps on arrival) or
UNMEASURABLE (carrying the reason). A collection that cannot answer is a
finding, not a silent omission.

A QDRANT GAP IS NOT AUTOMATICALLY A FLEET GAP
----------------------------------------------
A collection may be perfectly healthy in a store this probe never reads. The
wallet-label corpora are declared ``internal://neo4j/wallet-labels`` and the
market/telemetry corpora are ClickHouse-backed. A Qdrant-side absence for one
of those is reported ``off_qdrant`` with its declared home, never as staleness —
calling a well-fed Neo4j corpus stale is a false alarm that trains people to
ignore this report.

Measured 2026-09-28 against live Qdrant :6334: 96 collections, 6,216,516
points, 15 MEASURED and 81 UNMEASURABLE. 53 of the blind collections hold
5,089,017 points — 81.9% of the corpus by mass, led by ``codebase_reference_v2``
at 2,130,842. Full per-item reasons and what each would take to measure:
``docs/FRESHNESS-UNBLIND-2026-09-28.md``.

Reads GET and scroll only. Never writes, upserts, deletes or re-indexes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from rugbrain.freshness_classify import INGEST_FIELDS, is_ingest_field
from rugbrain.freshness_report import (
    OFF_QDRANT_HOMES,
    FreshnessVerdict,
    Measurement,
    OffQdrantStore,
    Probe,
    UnmeasurableReason,
    verdict_for,
)
from rugbrain.freshness_transport import (
    DEFAULT_QDRANT_URL,
    QdrantError,
    collection_info,
    iter_points,
    list_collections,
    parse_timestamp,
    payload_leaves,
)


def _unmeasurable(
    name: str, points: int, reason: str, store: OffQdrantStore | None = None
) -> Probe:
    return Probe(
        collection=name,
        points=points,
        verdict=FreshnessVerdict.UNMEASURABLE,
        measurement=Measurement.UNMEASURABLE,
        reason=reason,
        store=store,
    )


def probe_collection(
    name: str,
    url: str = DEFAULT_QDRANT_URL,
    *,
    cap: int = 250,
    page: int = 50,
    timeout: float = 40.0,
) -> Probe:
    """Read one collection's newest ingest time, or say why there isn't one.

    Fails CLOSED: an unreachable collection or a scroll error yields
    ``probe_error``, never an invented age. An off-Qdrant collection short-
    circuits before any read, because its Qdrant contents are not its truth.
    """
    if name in OFF_QDRANT_HOMES:
        return _unmeasurable(name, 0, UnmeasurableReason.OFF_QDRANT, OFF_QDRANT_HOMES[name])
    try:
        points = collection_info(name, url, timeout)["points"]
    except QdrantError as exc:
        return _unmeasurable(name, 0, f"{UnmeasurableReason.PROBE_ERROR}: {exc}")
    try:
        newest, field, scanned, dated = _scan(name, url, cap, page, timeout)
    except QdrantError as exc:
        return _unmeasurable(name, points, f"{UnmeasurableReason.PROBE_ERROR}: {exc}")
    if newest is None:
        reason = UnmeasurableReason.NO_INGEST_FIELD if points else UnmeasurableReason.EMPTY
        return Probe(
            collection=name,
            points=points,
            verdict=FreshnessVerdict.UNMEASURABLE,
            measurement=Measurement.UNMEASURABLE,
            reason=str(reason),
            scanned=scanned,
            dated=dated,
        )
    return Probe(
        collection=name,
        points=points,
        verdict=verdict_for(newest),
        measurement=Measurement.MEASURED,
        reason="ingest_field",
        newest=newest,
        field=field,
        scanned=scanned,
        dated=dated,
    )


def _scan(
    name: str, url: str, cap: int, page: int, timeout: float
) -> tuple[datetime | None, str | None, int, int]:
    """Newest ingest time across the sample, and which field supplied it.

    Fields are tried most-trusted first and the first usable one per point
    wins, so a collection carrying both ``ingested_at`` and ``ts`` is judged on
    ``ingested_at``.
    """
    newest: datetime | None = None
    field: str | None = None
    scanned = 0
    dated = 0
    for point in iter_points(name, url, cap=cap, page=page, timeout=timeout):
        scanned += 1
        flat = payload_leaves(point.get("payload"))
        for candidate in INGEST_FIELDS:
            if candidate not in flat:
                continue
            if not is_ingest_field(candidate, name):
                continue
            got = parse_timestamp(flat[candidate])
            if got is None:
                continue
            dated += 1
            if newest is None or got > newest:
                newest, field = got, candidate
            break
    return newest, field, scanned, dated


def build_report(
    url: str = DEFAULT_QDRANT_URL,
    *,
    cap: int = 250,
    page: int = 50,
    timeout: float = 40.0,
    only: Iterable[str] | None = None,
) -> list[Probe]:
    """Probe every collection and return one verdict per collection."""
    names = sorted(only) if only is not None else list_collections(url, timeout)
    return [probe_collection(n, url, cap=cap, page=page, timeout=timeout) for n in names]
