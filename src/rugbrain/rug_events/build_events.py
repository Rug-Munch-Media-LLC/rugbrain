"""Project the rug telemetry already in Qdrant into ``rug_events``.

Sources, all already-owned collections in this repo (READ ONLY):

  * ``defi_exploits`` — filter ``source == deflabx_solrpds`` (1,858 rows, live
    2026-09-28): typed ``metadata`` with liquidity, ratio and pool activity.
  * ``exploits``       — the same SolRPDS dataset reached through the PoC
    wrapper; ``metadata`` is a stringified dict and the values are also in
    ``code_block`` (137 rows).

Deduplication is by ``event_id`` (``deflabx_solrpds:<token>:<year>``), the
identity the source itself uses, so a token that appears in both collections
contributes one event with the higher-confidence payload — not two rows.

This module does NOT write. It returns the payloads; the caller embeds and
upserts. That split keeps embedding (expensive, needs a model) out of the
unit-testable logic and out of the extractor's contract.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.rug_events.deployers import apply_prior_counts
from rugbrain.rug_events.qdrant_io import DEFAULT_URL, scroll_all
from rugbrain.rug_events.schema import validate
from rugbrain.rug_events.solrpds import SOURCE, build_event

log = logging.getLogger(__name__)

#: (collection, payload filter) for each source of rug telemetry.
SOURCES: list[tuple[str, dict[str, Any] | None]] = [
    ("defi_exploits", {"must": [{"key": "source", "match": {"value": SOURCE}}]}),
    ("exploits", None),
]


def _dedupe_key(event: dict[str, Any]) -> str:
    return str(event.get("event_id") or event.get("token_address") or "")


def dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse to one event per ``event_id``, keeping the highest confidence."""
    best: dict[str, dict[str, Any]] = {}
    for event in events:
        key = _dedupe_key(event)
        if not key:
            continue
        current = best.get(key)
        if current is None or float(event.get("confidence") or 0) > float(
            current.get("confidence") or 0
        ):
            best[key] = event
    return list(best.values())


def project_point(point: dict[str, Any]) -> dict[str, Any] | None:
    """One source point -> one rug event, or None when it is not a rug event."""
    payload = point.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    if payload.get("collection") and "rug_events" in str(payload.get("collection")):
        return None  # already projected
    return build_event(payload, str(point.get("id") or ""))


def project_collection(
    base: str = DEFAULT_URL,
    sources: list[tuple[str, dict[str, Any] | None]] | None = None,
) -> list[dict[str, Any]]:
    """Read every source collection and return the deduped, validated events."""
    pairs = sources if sources is not None else SOURCES
    raw: list[dict[str, Any]] = []
    for collection, query_filter in pairs:
        points = scroll_all(base, collection, query_filter=query_filter)
        found = 0
        for point in points:
            event = project_point(point)
            if event is not None:
                raw.append(event)
                found += 1
        log.info(
            "project_collection name=%s scanned=%d rug_events=%d", collection, len(points), found
        )

    events = dedupe(raw)
    rejected = [e for e in events if validate(e)]
    if rejected:
        log.warning("project_rejected_invalid count=%d", len(rejected))
    events = [e for e in events if not validate(e)]
    apply_prior_counts(events)
    log.info("project_done events=%d", len(events))
    return events


__all__ = ["SOURCES", "dedupe", "project_collection", "project_point"]
