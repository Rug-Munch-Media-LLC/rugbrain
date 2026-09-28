"""``deployer_prior_rugs`` — is this deployer a serial rugger?

This is the highest-leverage field in the mandate and the reason the corpus
exists in structured form: "how much has THIS deployer taken" is unanswerable
while rug telemetry is prose.

Aggregation rule, stated precisely because the field is only as honest as its
definition: for a deployer with N attributed rug events, the events are
ordered by ``rug_ts`` and the **earliest** carries ``deployer_prior_rugs = 0``
(it is the first rug, there is no prior), while every subsequent event carries
the count of rugs *strictly earlier* than it. A deployer whose events all share
one timestamp is ordered by ``event_id`` so the result is deterministic rather
than dependent on scroll order.

Deployers with no attribution are left at 0 and flagged
``deployer_attributed = False`` — "we have never seen this address rug before"
is a different claim from "this address is clean", and only the first is
supportable from this corpus.
"""

from __future__ import annotations

from typing import Any


def order_key(event: dict[str, Any]) -> tuple[str, str]:
    """Deterministic chronological order: rug_ts, then event_id."""
    return (str(event.get("rug_ts") or ""), str(event.get("event_id") or ""))


def prior_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """Map every attributed event's ``event_id`` to its prior-rug count."""
    counts: dict[str, int] = {}
    by_deployer: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        deployer = str(event.get("deployer") or "").strip()
        if not deployer:
            continue
        by_deployer.setdefault(deployer, []).append(event)

    for deployer, group in by_deployer.items():
        for rank, event in enumerate(sorted(group, key=order_key)):
            counts[str(event.get("event_id") or id(event))] = rank
    return counts


def serial_ruggers(events: list[dict[str, Any]], minimum: int = 2) -> dict[str, int]:
    """Deployers with at least *minimum* attributed rugs -> total rug count."""
    totals: dict[str, int] = {}
    for event in events:
        deployer = str(event.get("deployer") or "").strip()
        if deployer:
            totals[deployer] = totals.get(deployer, 0) + 1
    return {d: n for d, n in totals.items() if n >= minimum}


def apply_prior_counts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp ``deployer_prior_rugs`` (and attribution) onto every event, in place.

    Every row also carries ``deployer_prior_rugs_known``, so a consumer reading
    the count alone cannot mistake "unknown" for "clean". A retriever or an
    agent that filters on ``deployer_prior_rugs == 0`` and ignores the flag
    would otherwise be asserting a fact this corpus does not contain — which is
    the specific failure the flag exists to make impossible to make silently.
    """
    from rugbrain.rug_events.schema import DEPLOYER_STATUS

    counts = prior_counts(events)
    for event in events:
        event_id = str(event.get("event_id") or "")
        attributed = bool(str(event.get("deployer") or "").strip())
        event["deployer_prior_rugs"] = counts.get(event_id, 0)
        event["deployer_attributed"] = attributed
        event["deployer_prior_rugs_known"] = attributed
        event["deployer_attribution_note"] = "" if attributed else DEPLOYER_STATUS
    return events


__all__ = ["apply_prior_counts", "order_key", "prior_counts", "serial_ruggers"]
