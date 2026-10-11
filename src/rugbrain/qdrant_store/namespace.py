"""Name routing: interim shadow namespace, canonical lane, unified reads.

Three related name transformations, all pure functions of a collection name.
They are one module because they COMPOSE: a read of a consolidated collection
must first know the interim window, then the lane, and only then whether the
consolidated corpus absorbs the request. Splitting the three would put an
ordering invariant into three docstrings instead of one.
"""

from __future__ import annotations

import os
from typing import Any

from rugbrain.qdrant_store.lane_resolver import resolve_collection

# ── Interim collection namespace (2026-08-24 migration window) ─────────────
# During the v1→v2 re-embedding migration the active embedder may produce a
# different dimension than the canonical collections (free NVIDIA nemotron
# 2048-dim vs qwen3-embedding-8b 2560-dim). Vectors from different models
# must NEVER mix (see _resize / TOP-100 #25), so while
# RAG_INTERIM_COLLECTION_SUFFIX is set, ALL reads and writes route to a
# suffixed shadow namespace auto-created at the live embedder's native dim.
# Remove the env var once rag-embed-migration.service completes, then restart.
_INTERIM_SUFFIX: str = os.getenv("RAG_INTERIM_COLLECTION_SUFFIX", "")

# ── Unified reads (2026-09-20 consolidation) ───────────────────────────────
# Every legacy RAG collection was copied into ``rag_documents`` with the
# source recorded in the ``collection`` payload field (content-hash doc ids,
# 334k points). With RAG_UNIFIED_READS=1 reads for a consolidated collection
# are served from the unified store with an equivalent payload filter — the
# legacy collections stay intact until retirement, so this is reversible by
# flipping one env var.
UNIFIED_COLLECTION = "rag_documents"
UNIFIED_READS = os.getenv("RAG_UNIFIED_READS", "0").lower() in ("1", "true", "yes")

CONSOLIDATED_SOURCES: frozenset[str] = frozenset(
    {
        "news_v2",
        "news_articles",
        "social_intel",
        "known_scams",
        "scam_patterns",
        "scam_intel_v2",
        "wallet_profiles",
        "wallet_v2",
        "defi_exploits",
        "threat_intel_v2",
        "contract_audits",
        "crypto_vulnerabilities",
        "regulation_enforcement",
        "research_v2",
        "education",
        "github_audits",
        "contract_security",
        "security_feeds",
        "market_health",
        "stablecoin_market",
        "live_threats",
        "hacks_v2",
    }
)


# ── Canonical-lane resolution (2026-09-28) ─────────────────────────────────
# ``_map_collection`` is the single choke point every read, has_point, scroll and
# insert passes through, so the base -> ``_8b`` rewrite is applied here once
# rather than in each caller. Order matters: the interim suffix is applied
# OUTERMOST, so a name arriving as ``threat_intel_v2`` becomes
# ``threat_intel_v2_8b`` first and the interim shadow then wraps that
# (``threat_intel_v2_8b<interim>``). A name arriving as
# ``threat_intel_v2<interim>`` is left alone by the resolver (it does not end in
# ``_8b`` and is not a declared base) and is only shadow-routed, which preserves
# the existing interim behaviour exactly.
#
# A name absent from the declared lane table is NOT rewritten and NOT rejected:
# it passes through, and ``declared_lane_dim`` reports None for it. A caller
# that knows its name must resolve calls ``lane_resolver.require_resolved``.
# See lane_resolver for the live-Qdrant evidence behind the mapping.


def map_collection(collection: str | None) -> str | None:
    """Route a declared name to the live target: interim shadow, then lane.

    The interim suffix is read from the PACKAGE namespace at call time, not from
    this module's global, for the reason the package docstring gives: tests patch
    ``rugbrain.qdrant_store._INTERIM_SUFFIX`` and internal callers resolve shared
    names through the package. Reading the local global here would leave the
    patch invisible — the same split-namespace hazard the pre-existing gate code
    avoided by taking the suffix as a parameter.
    """
    import rugbrain.qdrant_store as _pkg

    resolved = resolve_collection(collection)
    interim = _pkg._INTERIM_SUFFIX
    if resolved and interim and not resolved.endswith(interim):
        return f"{resolved}{interim}"
    return resolved


def unified_route(collection: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """Map a consolidated collection to (rag_documents, provenance filter).

    Returns the collection unchanged (and no filter) when unified reads are
    disabled or the name is not consolidated.
    """
    if UNIFIED_READS and collection in CONSOLIDATED_SOURCES:
        return UNIFIED_COLLECTION, {"key": "collection", "match": {"value": collection}}
    return map_collection(collection), None


__all__ = [
    "CONSOLIDATED_SOURCES",
    "UNIFIED_COLLECTION",
    "UNIFIED_READS",
    "map_collection",
    "unified_route",
]
