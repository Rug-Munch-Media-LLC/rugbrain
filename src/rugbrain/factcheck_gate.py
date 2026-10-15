"""Publish-path adapter over the deterministic fact-check gate.

:mod:`rugbrain.factcheck` owns the four-step check (extract → bind →
corroborate → verdict) and returns a plain report dict. The content engine
needs a small, typed result it can branch on before a draft is published, so
this module is the single seam between the two: one function,
:func:`fact_check_gate`, mapping the report to a :class:`FactCheckResult`.

Kept separate from :mod:`rugbrain.factcheck` so each module stays one job and
under the 200-line cap. Pure Python, no network — the gate is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rugbrain.factcheck import check_draft

__all__ = ["FactCheckResult", "fact_check_gate"]


@dataclass
class FactCheckResult:
    """Draft-level gate result consumed by the content engine.

    ``verdict`` is ``PASS`` (at least one claim corroborated), ``FAIL``
    (claims present but none corroborated — block the draft) or ``SKIP``
    (no verifiable claims — opinion, nothing to check, pass through).
    ``stripped_claims`` are the flagged claim values; ``unverifiable_claims``
    and ``missing`` are named aliases so callers can log either intent.
    """

    verdict: str
    claims: list[str] = field(default_factory=list)
    stripped_claims: list[str] = field(default_factory=list)
    unverifiable_claims: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _story_sources(story: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the source list the gate binds against from one story dict.

    A story carries its own ``url``/``content``/``title``; any ``sources``
    list already attached is appended so a caller with richer provenance
    (multiple outlets) gets it counted. Deduplicated by url.
    """
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in [story, *(story.get("sources") or [])]:
        if not isinstance(candidate, dict):
            continue
        url = str(candidate.get("url") or candidate.get("source_url") or "")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        sources.append(candidate)
    return sources


def fact_check_gate(text: str, story: dict[str, Any]) -> FactCheckResult:
    """Run :func:`check_draft` and map its report to a typed result.

    The content engine calls this before a draft is published. A draft with
    no verifiable claims is ``SKIP`` (pass through — opinion is not a claim);
    a draft whose claims are all uncorroborated is ``FAIL`` (block); a draft
    with at least one corroborated claim is ``PASS`` with the flagged claims
    listed for stripping.
    """
    report = check_draft(text, _story_sources(story))
    claims = report["claims"]
    passed = [c["value"] for c in claims if c["verdict"] == "pass"]
    flagged = [c["value"] for c in claims if c["verdict"] == "flag"]
    if not claims:
        verdict = "SKIP"
    elif passed:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return FactCheckResult(
        verdict=verdict,
        claims=passed,
        stripped_claims=flagged,
        unverifiable_claims=flagged,
        missing=flagged,
    )
