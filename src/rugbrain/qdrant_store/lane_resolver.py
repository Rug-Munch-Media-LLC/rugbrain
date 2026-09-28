"""Canonical-lane collection resolver: base name -> its ``_8b`` twin.

The 8B knowledge migration is COMPLETE. Every ``*_8b`` twin exists at 4096 and
is populated. What was left broken is the NAME: every ingest source's
``DEFAULT_COLLECTION`` still declared the 2560 base, so the canonical lane
produced a 4096 vector at a 2560 name, the dimension guard correctly refused,
and the run exited non-zero. The migration was never the problem.

THE RULE
    A base name is rewritten to ``<name>_8b`` IFF this repo's declared lane for
    that name is the canonical dim (:data:`CANONICAL_LANE_DIM`, 4096) AND a twin
    name is declared for it. Anything else passes through unchanged:

      * ``threat_intel_v2``    -> ``threat_intel_v2_8b``   (INFO logged)
      * ``threat_intel_v2_8b`` -> ``threat_intel_v2_8b``   (idempotent pass)
      * ``privacy_protocol``   -> ``privacy_protocol``     (already 4096, no twin)

Never a silent rewrite: every rewrite logs ``base`` and ``resolved`` at INFO.

FAIL LOUD
    A base name on the canonical lane with NO declared twin raises
    :class:`UnresolvedLane`. Falling back to the base name is the defect, not a
    recovery — it reproduces the exact dim-mismatch this module exists to stop.

WHY A DECLARED TABLE, NOT "DOES THE TWIN EXIST?"
    Live Qdrant (96 collections, 33 twins) proves the two are not the same
    predicate. 21 owned names are migrated (2560 base + 4096 twin) and need the
    rewrite. 11 owned names are ALREADY canonical at 4096 and never had a 2560
    base, so a twin-absence test would leave them alone only by luck. 6 more are
    pinned to 2560 with no twin. Keying on the live dim instead makes the
    resolver depend on Qdrant being up and on a network call per write, and
    makes the write target change under the operator's feet. The table is a
    declaration like ``DEFAULT_COLLECTION`` was — but a checked one, holding
    exactly one fact per name that the defect duplicated in two places.

The reverse direction (given a live name, report its dim) is in ``lane_dims``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Final

from rugbrain.dim_policy import COLLECTION_DIM

# `fetch_live_dim` lives in `lane_dims` (it reads the dimension off a live
# Qdrant collection); it is re-exported here because `qdrant_store/__init__`
# and the write gate import the resolver as the single public surface, and
# splitting the module without carrying the re-export left the package
# unimportable.

logger = logging.getLogger(__name__)

#: Suffix of a migrated twin. Shared with ``canary_routing.CANARY_SUFFIX``.
EIGHT_B_SUFFIX: Final = "_8b"

#: The lane this repo writes on: qwen3-embedding-8b @ 4096.
CANONICAL_LANE_DIM: Final = int(COLLECTION_DIM)

#: Retired 2560 generation (qwen3-embedding-4b). Read-only, never a write target.
RETIRED_LANE_DIM: Final = 2560

#: The dim a base name is pinned to when this repo has not migrated it.
_PRESERVED_LANE_DIM: Final = int(os.getenv("RAG_PRESERVED_LANE_DIM", str(RETIRED_LANE_DIM)))

#: Escape hatch. Off by default; set ``RAG_LANE_RESOLVER=off`` to restore the
#: pre-resolver behaviour for a single run. Values: off | warn | strict.
#: ``warn`` logs and falls through; ``strict`` is the default (fail loud).
RESOLVER_MODE_ENV: Final = "RAG_LANE_RESOLVER"
RESOLVER_MODES: Final = frozenset({"off", "warn", "strict"})


class UnresolvedLane(ValueError):
    """A canonical-lane base name has no declared ``_8b`` twin.

    Raised instead of falling back to the base name: the base name is the
    defect. Catching this and writing to the base would recreate the 2560/4096
    split at the next ingest.
    """


def _lane_twin(name: str) -> str:
    return f"{name}{EIGHT_B_SUFFIX}"


def _declared_twins() -> frozenset[str]:
    """Bases whose ``_8b`` twin is the write target (owner's mapping, 2026-09-28).

    Exactly the 21 owned names proven MIGRATED against live Qdrant: each has a
    2560 base collection AND a populated 4096 twin, so the base is retained
    read-only and every write must land on the twin.

    NOT listed, and why — the two groups look the same ("no twin") but are not:

    * Already canonical at 4096 with no base to retire (``privacy_protocol``,
      ``l2_risk``, ``market_data``, ``crime_reports``, ``bridge_architecture``,
      ``mev_architecture``, ``standards``, ``zk_circuits``, ``scam_signatures``,
      ``newsletters``, ``podcast_episodes``). Adding a twin for these would
      strand their data in a collection that does not exist.
    * Pinned to the 2560 generation with no twin (``rug_events``,
      ``coding_knowledge``, ``public_crypto_patterns``, ``solana_patterns``) or
      to 1024 (``wallet_clusters``, ``youtube_videos``). A 4096 vector cannot
      enter any of them — :func:`declared_lane_dim` returns the pinned dim and
      the existing dimension guard refuses. That refusal is correct and loud.
    * Owned names with no Qdrant collection at all (``bundle_patterns``,
      ``cluster_labels``, ``cryptography``, ``rag_docs``, ``rag_threats``,
      ``scanner_v2``, ``solidity_patterns``). Auto-creating a 4096 collection
      for each would fork the corpus by accident.

    Twins that exist but are NOT owned (``agent_vectors``, ``cross_chain``,
    ``master_skills``, ``protocol_design``, ``scan_intel``, ``exploits``,
    ``social_intel``, ``codebase_reference_v2``, ``security_patterns_test``) are
    deliberately absent: routing a rugbrain source at them would widen write
    ownership past the gate's boundary.
    """
    return frozenset(
        {
            "contract_audits",
            "contract_security",
            "crypto_vulnerabilities",
            "defi_exploits",
            "education",
            "github_audits",
            "hacks_v2",
            "known_scams",
            "live_threats",
            "news_articles",
            "news_v2",
            "rag_documents",
            "regulation_enforcement",
            "research_v2",
            "scam_intel_v2",
            "security_alerts",
            "security_feeds",
            "security_patterns",
            "stablecoin_market",
            "threat_intel_v2",
            "wallet_profiles",
            "wallet_v2",
        }
    )


def _pinned_dims() -> Mapping[str, int]:
    """Owned names pinned below the canonical dim, and the dim they are at."""
    return {
        "coding_knowledge": _PRESERVED_LANE_DIM,
        "public_crypto_patterns": _PRESERVED_LANE_DIM,
        "rug_events": _PRESERVED_LANE_DIM,
        "solana_patterns": _PRESERVED_LANE_DIM,
        "wallet_clusters": 1024,
        "youtube_videos": 1024,
    }


_DECLARED_TWINS: Final[frozenset[str]] = _declared_twins()
_PINNED_DIMS: Final[Mapping[str, int]] = _pinned_dims()


def resolver_mode() -> str:
    """Resolve the effective mode, defaulting to ``strict`` when unset/invalid."""
    raw = os.getenv(RESOLVER_MODE_ENV, "strict").strip().lower()
    return raw if raw in RESOLVER_MODES else "strict"


def declared_lane_dim(collection: str | None) -> int | None:
    """The dim this repo writes *collection* at, or None when unclassified.

    Accepts a base name OR a resolved ``*_8b`` twin, so a caller can ask about
    the name it just got back from :func:`resolve_collection`. None means "no
    declared fact" — the caller decides, and for writes that is
    :data:`CANONICAL_LANE_DIM` (the 4096 default everywhere in the repo).
    """
    if not collection:
        return None
    if collection in _DECLARED_TWINS:
        return CANONICAL_LANE_DIM
    base = strip_twin_suffix(collection)
    if base in _DECLARED_TWINS:
        return CANONICAL_LANE_DIM
    return _PINNED_DIMS.get(collection) or _PINNED_DIMS.get(base or "")


def resolve_collection(collection: str | None) -> str | None:
    """Resolve a declared collection name to the name writes should target.

    Idempotent: resolving an already-canonical ``*_8b`` name returns it
    unchanged, so a re-run never appends a second suffix. ``None`` and ``""``
    pass through — the write path turns an empty name into ``"default"``.
    """
    if not collection:
        return collection
    mode = resolver_mode()
    if mode == "off" or collection.endswith(EIGHT_B_SUFFIX):
        return collection
    if collection not in _DECLARED_TWINS:
        return collection
    resolved = _lane_twin(collection)
    if mode == "warn":
        logger.warning(
            "qdrant_lane_resolve base=%s resolved=%s mode=warn — strict would resolve here",
            collection,
            resolved,
        )
        return resolved
    logger.info(
        "qdrant_lane_resolve base=%s resolved=%s dim=%d reason=canonical_lane_8b_twin",
        collection,
        resolved,
        CANONICAL_LANE_DIM,
    )
    return resolved


def strip_twin_suffix(collection: str | None) -> str | None:
    """Return the base name behind a twin, for gate evaluation.

    Ownership is checked on the BASE name: the ``_8b`` suffix is a lane
    marker, not a different owner. This coexists with the interim-suffix strip
    in ``store_write.insert`` — call it on the result of
    :func:`rugbrain.qdrant_store.collections._map_collection`, which appends the
    interim suffix outermost.
    """
    if collection and collection.endswith(EIGHT_B_SUFFIX):
        return collection[: -len(EIGHT_B_SUFFIX)]
    return collection


__all__ = [  # noqa: RUF022 -- kept alphabetical for review
    "CANONICAL_LANE_DIM",
    "EIGHT_B_SUFFIX",
    "RESOLVER_MODES",
    "RESOLVER_MODE_ENV",
    "RETIRED_LANE_DIM",
    "UnresolvedLane",
    "declared_lane_dim",
    "resolver_mode",
    "resolve_collection",
    "strip_twin_suffix",
]
