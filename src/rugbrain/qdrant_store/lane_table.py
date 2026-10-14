"""The declared facts of the canonical lane: which bases have an ``_8b`` twin.

Split from ``lane_resolver`` so the table and its evidence can be reviewed as
one artefact, and so the resolver stays under the 200-line cap.

Every entry below is verified against live Qdrant (``http://rmi-qdrant-v2:6334``,
96 collections, 33 twins, 2026-09-28). The reasoning is repeated in each table's
docstring because the two groups look identical from the outside — "no twin" —
and are not.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Final

from rugbrain.dim_policy import COLLECTION_DIM

#: The lane this repo writes on: qwen3-embedding-8b @ 4096.
CANONICAL_LANE_DIM: Final[int] = int(COLLECTION_DIM)

#: Retired generation (qwen3-embedding-4b), retained read-only. Overridable only
#: so a test can pin the table without depending on the live default.
PRESERVED_LANE_DIM: Final[int] = int(os.getenv("RAG_PRESERVED_LANE_DIM", "2560"))


def _lane_twin(name: str) -> str:
    return f"{name}_8b"


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
      for each would fork the corpus by accident. An unlisted name is therefore
      NOT a lane error: it passes through and ``declared_lane_dim`` reports
      ``None`` for it. Use ``require_resolved`` where a pass-through would
      mean a declared write source silently missed the canonical lane.

    Twins that exist but are NOT owned (``agent_vectors``, ``cross_chain``,
    ``master_skills``, ``protocol_design``, ``exploits``, ``social_intel``,
    ``codebase_reference_v2``, ``security_patterns``, ``security_patterns_test``)
    are deliberately
    absent: routing a rugbrain source at them would widen write ownership past
    the gate's boundary.

    ``scan_intel`` WAS in that not-owned list, and that omission was the live
    P0. Measured 2026-09-30 against live Qdrant (97 collections): the 2560 base
    held 60,483 points and had been GROWING, while ``scan_intel_8b`` held 0.
    Every new scan therefore landed on the retired lane and the canonical lane
    returned nothing. The cause was not the resolver — ``resolve_collection``
    consults THIS table, so a name absent from it passes through to the 4B base
    by construction. The absence was a stale ownership boundary written when the
    twin was empty; once the twin is populated the name is genuinely owned, and
    leaving it out is what keeps the write path on the dead generation. The fix
    belongs in the declaration, not in a second env override that the next
    env_file precedence race would win over.
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
            "market_health",
            "news_articles",
            "news_v2",
            "rag_documents",
            "regulation_enforcement",
            "research_v2",
            "scam_patterns",
            "scan_intel",
            "scam_intel_v2",
            "security_alerts",
            "security_feeds",
            "stablecoin_market",
            "threat_intel_v2",
            "wallet_profiles",
            "wallet_v2",
        }
    )


def _pinned_dims() -> Mapping[str, int]:
    """Owned names pinned below the canonical dim, and the dim they are at."""
    return {
        "coding_knowledge": PRESERVED_LANE_DIM,
        "public_crypto_patterns": PRESERVED_LANE_DIM,
        "rug_events": PRESERVED_LANE_DIM,
        "solana_patterns": PRESERVED_LANE_DIM,
        "wallet_clusters": 1024,
        "youtube_videos": 1024,
    }


_DECLARED_TWINS: Final[frozenset[str]] = _declared_twins()
_PINNED_DIMS: Final[Mapping[str, int]] = _pinned_dims()


#: Names whose ``_8b`` twin is the write target.
DECLARED_TWINS: Final[frozenset[str]] = _declared_twins()

#: Owned names pinned below the canonical dim, and the dim they are at.
PINNED_DIMS: Final[Mapping[str, int]] = _pinned_dims()

__all__ = [
    "CANONICAL_LANE_DIM",
    "DECLARED_TWINS",
    "PINNED_DIMS",
    "PRESERVED_LANE_DIM",
]
