"""Factual-consensus scoring for a story's sources.

Split out of :mod:`rugbrain.news_clustering` (2026-10-25, size ratchet).
"""

from __future__ import annotations

from typing import Any

from rugbrain.news_cluster_core import StorySource, _clamp


def _factual_consensus(sources: list[StorySource]) -> tuple[float, dict[str, Any]]:
    """Estimate agreement between sources on verifiable facts (0-1).

    Extracts verifiable claims (addresses, tx hashes, amounts, percentages,
    protocols) from each source summary and compares them across sources.
    A story where the same claims recur in many sources has high consensus;
    contradictory / single-source claims lower it.

    Returns ``(consensus, detail)``. ``consensus=0.5`` when there are no
    verifiable claims to compare (neutral — can't disagree on nothing).
    """
    from rugbrain.news_rating import _find_claims

    claim_sets: list[set[tuple[str, str]]] = []
    total_sources_with_claims = 0
    for src in sources:
        text = f"{src.title}. {src.summary}"
        claims = _find_claims(text)
        flat: set[tuple[str, str]] = set()
        for category, values in claims.items():
            for value in values:
                flat.add((category, str(value).lower()))
        claim_sets.append(flat)
        if flat:
            total_sources_with_claims += 1

    if not claim_sets:
        return 0.5, {"method": "no-claims", "sources_with_claims": 0}

    # Union of all claims across sources; count how many sources support each.
    all_claims: dict[tuple[str, str], int] = {}
    for cs in claim_sets:
        for claim in cs:
            all_claims[claim] = all_claims.get(claim, 0) + 1

    if not all_claims:
        return 0.5, {"method": "no-claims", "sources_with_claims": total_sources_with_claims}

    supported = sum(1 for count in all_claims.values() if count >= 2)
    total = len(all_claims)
    consensus = supported / total if total else 0.5
    # Weight consensus toward stories corroborated by multiple sources.
    if total_sources_with_claims >= 3:
        consensus = _clamp(consensus * 0.9 + 0.1)
    detail = {
        "method": "claim-overlap",
        "sources_with_claims": total_sources_with_claims,
        "total_unique_claims": total,
        "multi_source_claims": supported,
        "single_source_claims": total - supported,
        "sample_claims": sorted({c[1] for c in all_claims})[:10],
    }
    return round(consensus, 4), detail
