"""First-source latency report over the cluster state.

Latency is only computed when the source states a publication time; an
unparseable or absent timestamp yields no sample rather than a zero.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from rugbrain.zeroday.schema import iso_utc, parse_ts


def _lag_seconds(observed: datetime, published: str | None) -> int | None:
    published_dt = parse_ts(published)
    if published_dt is None or observed < published_dt:
        return None
    return int((observed - published_dt).total_seconds())


def latency_report(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Per-source observation lag, plus cross-source spread on merged clusters."""
    per_source: dict[str, list[int]] = {}
    first_seen_by_source: dict[str, int] = {}
    multi_source = 0
    spreads: list[int] = []
    for cluster in state["clusters"].values():
        members = list(cluster.get("members", {}).values())
        sources = {m.get("source") for m in members}
        if len(sources) > 1:
            multi_source += 1
            stamps = [
                stamp
                for stamp in (parse_ts(m.get("first_seen")) for m in members)
                if stamp is not None
            ]
            if len(stamps) > 1:
                spreads.append(int((max(stamps) - min(stamps)).total_seconds()))
        for member in members:
            source = str(member.get("source") or "")
            first_seen_by_source[source] = first_seen_by_source.get(source, 0) + 1
            lag = _lag_seconds(
                parse_ts(member.get("first_seen")) or now, member.get("published_at")
            )
            if lag is not None:
                per_source.setdefault(source, []).append(lag)
    return {
        "generated_at": iso_utc(now.timestamp()),
        "clusters": len(state["clusters"]),
        "multi_source_clusters": multi_source,
        "cross_source_spread_median_s": int(median(spreads)) if spreads else None,
        "first_seen_per_source": first_seen_by_source,
        "median_lag_s_per_source": {
            source: int(median(values)) for source, values in sorted(per_source.items())
        },
        "lag_samples_per_source": {source: len(values) for source, values in per_source.items()},
    }
