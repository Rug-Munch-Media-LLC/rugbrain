"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Verification gate. A brief exists only when ALL of these hold:

1. at least two independent sources — distinct registrable domains, not two
   URLs from one publisher;
2. at least one of them is on-chain (contract/holder/deployer data);
3. every counted evidence row is dated and no older than 30 days;
4. the triage score clears the signal floor and the confidence floor.

A single heuristic, however loud, fails rule 1 by construction. That is the
intended refusal path, not an error path.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from rugbrain.scam_watch.models import EvidenceItem, Signal, Verdict

MIN_INDEPENDENT_SOURCES = 2
MIN_SIGNAL_SCORE = 0.50
MIN_CONFIDENCE = 0.60
EVIDENCE_MAX_AGE = timedelta(hours=720)

_TWO_PART_SUFFIXES = frozenset({"co.uk", "org.uk", "com.au", "co.jp", "com.br", "co.nz"})


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_current(item: EvidenceItem, now: datetime) -> bool:
    age = now - item.captured_at
    return timedelta(0) <= age <= EVIDENCE_MAX_AGE


def assess(
    signals: Sequence[Signal],
    evidence: Sequence[EvidenceItem],
    now: datetime | None = None,
) -> Verdict:
    """Decide whether the collected evidence can support a draft brief."""
    moment = now or datetime.now(UTC)
    current = tuple(item for item in evidence if _is_current(item, moment))
    stale = len(evidence) - len(current)
    domains = {_domain(item.url) for item in current} - {""}
    onchain = any(item.kind == "onchain" for item in current)
    score = min(1.0, sum(signal.weight for signal in signals))
    # Two independent sources is the publishable bar, so it carries full
    # weight; a lone source already fails rule 1 and is discounted here too.
    confidence = round(min(1.0, score * min(1.0, 0.90 + 0.05 * len(domains))), 3)

    failures: list[str] = []
    if score < MIN_SIGNAL_SCORE:
        failures.append("insufficient_signals")
    if len(domains) < MIN_INDEPENDENT_SOURCES:
        failures.append("fewer_than_two_independent_sources")
    if not onchain:
        failures.append("no_onchain_evidence")
    if stale:
        failures.append(f"stale_evidence:{stale}")
    if confidence < MIN_CONFIDENCE:
        failures.append("confidence_below_floor")

    reasons = (
        f"score={score:.3f} (floor {MIN_SIGNAL_SCORE:.2f})",
        f"{len(domains)} independent source(s): {', '.join(sorted(domains)) or 'none'}",
        f"on-chain evidence: {'yes' if onchain else 'no'}",
        f"{len(current)} current row(s), {stale} stale row(s)",
    )
    return Verdict(
        publishable=not failures,
        confidence=confidence,
        independent_sources=len(domains),
        reasons=reasons,
        failures=tuple(failures),
    )
