"""Corpus exposure audit — the corpus we paid for must have a door.

WHY THIS EXISTS
---------------
2026-10-25 measurement: ``security_patterns_8b`` holds 14,275 points,
``live_threats_8b`` 11,164 and ``contract_audits_8b`` 1,050, yet no MCP tool
docstring, route default or manifest entry names any of them. An agent asking
"what bites us here" reaches ``threat_intel_v2`` and stops. Points without a
door are storage cost, not knowledge.

The second failure mode is subtler and was found in the same sweep: a declared
base name that resolves to a lane with ZERO points (``live_threats`` = 0 while
its ``_8b`` twin holds 11,164). The search call succeeds, returns ``[]``, and
nobody learns the corpus exists. Empty is not missing — it is a typed refusal.

ONE ANSWER PER QUESTION
-----------------------
``EXPOSED`` is the only place that says a collection can be reached, and by
what. ``audit_exposure`` is the only place that decides a corpus is covered.
The guard test (``tests/test_corpus_exposure_guard.py``) fails when a
collection grows past ``MIN_EXPOSED_POINTS`` with no entry here, so silence
cannot recur.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

#: Any collection above this many points is corpus, not a fixture — it must be
#: reachable. Below it a collection may be a test slice (316 ``dedup_slice_test``)
#: or a seed (15 ``solidity_patterns``) where exposure is not yet earned.
MIN_EXPOSED_POINTS: Final[int] = 1000

#: collection -> the exposure token naming HOW an agent reaches it.
#: ``search:<tool>``  a rugbrain_mcp tool accepts the name (default or param)
#: ``search:param``   reachable only through the freeform ``collection`` argument
#: ``route:<path>``   an HTTP route serves it directly
EXPOSED: Final[Mapping[str, str]] = {
    # --- MCP tools that name the collection in their default/body -----------
    "threat_intel_v2": "search:rugbrain_search(default)",
    "threat_intel_v2_8b": "search:rugbrain_search(alias-threat_intel_v2)",
    "scam_intel_v2": "search:rugbrain_search(docstring)",
    "scam_intel_v2_8b": "search:rugbrain_search(alias-scam_intel_v2)",
    "known_scams": "search:rugbrain_search(docstring)",
    "known_scams_8b": "search:rugbrain_search(alias-known_scams)",
    "scam_patterns": "search:rugbrain_scam_lookup",
    "scam_patterns_8b": "search:rugbrain_scam_lookup",
    "defi_exploits": "search:rugbrain_search(docstring)",
    "defi_exploits_8b": "search:rugbrain_search(alias-defi_exploits)",
    "news_v2": "search:rugbrain_sentiment",
    "news_v2_8b": "search:rugbrain_sentiment(alias)",
    "social_intel": "search:rugbrain_sentiment",
    "social_intel_8b": "search:rugbrain_sentiment(alias)",
    "education": "search:rugbrain_market_fusion",
    "education_8b": "search:rugbrain_market_fusion(alias)",
    "wallet_v2": "search:rugbrain_search(docstring)",
    "wallet_v2_8b": "search:rugbrain_search(alias-wallet_v2)",
    "crypto_vulnerabilities": "search:rugbrain_search(docstring)",
    "crypto_vulnerabilities_8b": "search:rugbrain_search(alias)",
    "regulation_enforcement": "search:rugbrain_search(docstring)",
    "regulation_enforcement_8b": "search:rugbrain_search(alias)",
    # --- reachable only through the freeform collection= argument -----------
    # Catalogued deliberately: param-only is a door nobody finds, so these are
    # listed in rugbrain_search's docstring options (2026-10-25).
    "security_patterns_8b": "search:param(rugbrain_search)",
    "security_patterns_8b_hybrid": "search:param(rugbrain_search)",
    "live_threats_8b": "search:param(rugbrain_search, lane=base+_8b)",
    "live_threats_8b_hybrid": "search:param(rugbrain_search, lane=base+_8b)",
    "contract_audits_8b": "search:param(rugbrain_search)",
    "contract_audits_8b_hybrid": "search:param(rugbrain_search)",
    "exploits_8b": "search:param(rugbrain_search)",
    "market_data": "route:/api/v1/market",
    # --- declared non-corpus (fixtures / runtime state) ---------------------
    "dedup_slice_test": "runtime-state",
    "security_patterns_test": "probe/test-fixture",
    "security_patterns_test_8b": "probe/test-fixture",
    "agent_memory": "runtime-state",
}

#: Base names whose own lane is empty but whose twin serves. Lookup, never
#: re-derivation: the twin is the canonical lane name (``<base>_8b``), matching
#: ``qdrant_store.lane_table._lane_twin``.
EMPTY_BASE_LANES: Final[Mapping[str, str]] = {
    "live_threats": "live_threats_8b",
    "exploits": "exploits_8b",
    "cross_chain": "cross_chain_8b",
    "crypto_sector_docs": "security_patterns_8b",
    "regulation_enforcement": "regulation_enforcement_8b",
}


class EmptyCorpus(LookupError):
    """Typed refusal: the lane asked for holds no points.

    Refusal, not ``[]`` — an empty list is indistinguishable from "no match",
    which is how a live corpus stayed invisible for a month.
    """

    def __init__(self, collection: str, servable: str | None) -> None:
        self.collection = collection
        self.servable = servable
        hint = f" (servable lane: {servable})" if servable else ""
        super().__init__(f"empty_collection:{collection}{hint}")


def servable_lane(collection: str, counts: Mapping[str, int]) -> str:
    """Resolve ``collection`` to a lane that actually holds points.

    Raises :class:`EmptyCorpus` when neither the name nor its twin has any —
    the caller must surface that, never an empty result list.
    """
    if counts.get(collection, 0) > 0:
        return collection
    twin = EMPTY_BASE_LANES.get(collection) or f"{collection}_8b"
    if counts.get(twin, 0) > 0:
        return twin
    raise EmptyCorpus(collection, None)


@dataclass(frozen=True)
class ExposureReport:
    """Point-count-weighted reachability of the corpus an agent can touch."""

    total_points: int
    reachable_points: int
    gaps: tuple[str, ...]

    @property
    def pct_reachable(self) -> float:
        return 0.0 if not self.total_points else 100.0 * self.reachable_points / self.total_points


def audit_exposure(
    counts: Mapping[str, int],
    exposed: Mapping[str, str] = EXPOSED,
    min_points: int = MIN_EXPOSED_POINTS,
) -> ExposureReport:
    """The single decision: which collections over ``min_points`` have no door.

    ``gaps`` is sorted by descending point count so the most expensive silence
    is named first. A collection listed in ``EXPOSED`` counts as reachable even
    when the token is ``runtime-state`` — the question is "does an agent have a
    path", and a fixture path is a path.
    """
    total = sum(max(0, n) for n in counts.values())
    gaps: list[tuple[int, str]] = []
    reachable = 0
    for name, n in counts.items():
        if n <= 0:
            continue
        if exposed.get(name):
            reachable += n
        elif n > min_points:
            gaps.append((n, name))
    gaps.sort(reverse=True)
    return ExposureReport(
        total_points=total,
        reachable_points=reachable,
        gaps=tuple(f"{name}({n})" for n, name in gaps),
    )
