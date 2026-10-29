"""Cross-source dedupe, change feed and first-source latency.

One cluster per real-world item. A record joins an existing cluster when any
of its alias keys is already known (so a GHSA advisory and the NVD CVE that
carry the same CVE-… collapse into one line). Every observation is stamped,
which is what makes first-source latency measurable instead of guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rugbrain.zeroday.latency import latency_report
from rugbrain.zeroday.schema import Record, iso_utc, parse_ts

STATE_VERSION = 1
MAX_CLUSTER_AGE_DAYS = 400


@dataclass(frozen=True, slots=True)
class Change:
    """One change-feed row."""

    event: str  # "new" | "updated"
    cluster_id: str
    record: Record
    first_seen: str
    lag_s: int | None
    cross_source_lag_s: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "cluster_id": self.cluster_id,
            "id": self.record.canonical_key(),
            "kind": self.record.kind,
            "severity": self.record.severity,
            "source": self.record.source,
            "title": self.record.title,
            "url": self.record.url,
            "published_at": self.record.published_at,
            "first_seen": self.first_seen,
            "lag_s": self.lag_s,
            "cross_source_lag_s": self.cross_source_lag_s,
        }


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Everything one sweep produced."""

    records: tuple[Record, ...]
    changes: tuple[Change, ...]
    state: dict[str, Any]
    latency: dict[str, Any]


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "clusters": {}, "alerts": {}}


def _lag_seconds(observed: datetime, published: str | None) -> int | None:
    published_dt = parse_ts(published)
    if published_dt is None or observed < published_dt:
        return None
    return int((observed - published_dt).total_seconds())


def _alias_index(clusters: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for cluster_id, cluster in clusters.items():
        for alias in cluster.get("aliases") or []:
            index.setdefault(str(alias), cluster_id)
    return index


def _new_cluster(record: Record, observed: datetime) -> dict[str, Any]:
    return {
        "id": record.canonical_key(),
        "kind": record.kind,
        "aliases": list(record.alias_keys()),
        "first_seen": iso_utc(observed.timestamp()),
        "first_source": record.source,
        "members": {},
    }


def _prune(clusters: dict[str, Any], now: datetime) -> None:
    cutoff = now - timedelta(days=MAX_CLUSTER_AGE_DAYS)
    stale = [
        cid
        for cid, cluster in clusters.items()
        if (parse_ts(cluster.get("first_seen")) or now) < cutoff
    ]
    for cid in stale:
        clusters.pop(cid, None)


def merge(
    state: dict[str, Any] | None,
    records: list[Record] | tuple[Record, ...],
    now: datetime | None = None,
) -> IngestResult:
    """Fold one sweep of records into the cluster state."""
    observed = now or datetime.now(UTC)
    current = state if isinstance(state, dict) and "clusters" in state else _empty_state()
    current.setdefault("alerts", {})
    clusters: dict[str, Any] = current["clusters"]
    index = _alias_index(clusters)
    changes: list[Change] = []
    seen_members: set[str] = set()

    for record in records:
        aliases = record.alias_keys()
        existing = next((index[a] for a in aliases if a in index), None)
        cluster_id = existing or record.canonical_key()
        if existing is None:
            clusters[cluster_id] = _new_cluster(record, observed)

        cluster = clusters[cluster_id]
        aliases_known = set(cluster.get("aliases") or [])
        aliases_known.update(aliases)
        cluster["aliases"] = sorted(aliases_known)
        for alias in cluster["aliases"]:
            index[alias] = cluster_id

        member_key = f"{record.source}|{record.url}"
        if member_key in seen_members:
            continue
        seen_members.add(member_key)
        member = cluster["members"].get(member_key, {})
        fingerprint = record.fingerprint()
        event: str | None = None
        if not member:
            event = "new"
        elif member.get("fingerprint") != fingerprint:
            event = "updated"
        first_seen = member.get("first_seen") or iso_utc(observed.timestamp()) or ""
        cluster["members"][member_key] = {
            "source": record.source,
            "kind": record.kind,
            "title": record.title,
            "url": record.url,
            "published_at": record.published_at,
            "severity": record.severity,
            "fingerprint": fingerprint,
            "first_seen": first_seen,
        }
        if event is None:
            continue
        cluster_first = parse_ts(cluster.get("first_seen")) or observed
        cross = None if not existing else int((observed - cluster_first).total_seconds())
        changes.append(
            Change(
                event=event,
                cluster_id=cluster_id,
                record=record,
                first_seen=first_seen,
                lag_s=_lag_seconds(observed, record.published_at),
                cross_source_lag_s=cross,
            )
        )

    _prune(clusters, observed)
    current["version"] = STATE_VERSION
    return IngestResult(
        records=tuple(records),
        changes=tuple(changes),
        state=current,
        latency=latency_report(current, observed),
    )


__all__ = [
    "MAX_CLUSTER_AGE_DAYS",
    "STATE_VERSION",
    "Change",
    "IngestResult",
    "merge",
]
