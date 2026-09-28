"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Collection routing — which Qdrant collection a document belongs in.

A document goes to exactly one collection, chosen by its dominant
signal. Routing on severity alone would send a phishing domain list and
a protocol exploit to the same place and pollute both retrieval paths.

The order in :func:`route_collection` is most-specific first, and the
comment on each branch is the reason it outranks the one below it.
"""

from __future__ import annotations

from typing import Final

from rugbrain.digest.entities import EntitySet

COLLECTION_NEWS: Final = "news_articles"
COLLECTION_SCAMS: Final = "known_scams"
COLLECTION_THREAT: Final = "threat_intel_v2"
COLLECTION_SCAM_INTEL: Final = "scam_intel_v2"

# Only these may be written. A caller cannot route a document into an
# arbitrary Qdrant collection by passing a name — the allowlist is the
# boundary between "ingest content" and "write to any collection in this
# Qdrant instance".
WRITABLE_COLLECTIONS: Final = frozenset(
    {COLLECTION_NEWS, COLLECTION_SCAMS, COLLECTION_THREAT, COLLECTION_SCAM_INTEL}
)

_HACK_TERMS: Final = ("exploit", "hack", "breach", "drained", "stolen funds", "vulnerability")
_PHISH_TERMS: Final = ("phishing", "drainer", "honeypot", "scam site", "impersonat")
_NEWS_TERMS: Final = ("announce", "partnership", "funding", "launch", "proposal", "upgrade")


def route_collection(text: str, entities: EntitySet) -> str:
    """Pick exactly one Qdrant collection for this document.

    Explicit and readable over a scored model: the decision is auditable
    in one glance, which matters when someone asks "why did this land in
    scam_intel_v2".
    """
    lowered = text.lower()
    has_hack = any(t in lowered for t in _HACK_TERMS)
    has_phish = any(t in lowered for t in _PHISH_TERMS)

    # A phishing DOMAIN is the most concrete artifact there is — a URL an
    # analyst can act on right now — so it outranks every other signal.
    if entities.domains and has_phish:
        return COLLECTION_SCAMS
    # A named protocol exploit is analysis, not an action-list entry. It
    # must be checked BEFORE the bare-address rule, or every exploit
    # writeup that quotes a stolen-funds address lands on the blocklist
    # collection and pollutes it with non-blocklist addresses.
    if has_hack:
        return COLLECTION_THREAT
    # A critical-severity document naming an address, with no exploit
    # framing, is a scam report: that address is the deliverable.
    if entities.severity == "critical" and entities.addresses:
        return COLLECTION_SCAM_INTEL
    if has_phish:
        return COLLECTION_SCAMS
    if entities.severity in ("critical", "high"):
        return COLLECTION_THREAT
    return COLLECTION_NEWS
