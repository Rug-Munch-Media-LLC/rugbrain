"""Declared corpora, group: scam.

Scam corpora and wallet labels.

Every row is transcribed from ``docs/COVERAGE-AUDIT-2026-09-28.md`` §1 (96 collections,
6,314,262 points, dims {1024, 2560, 4096}). LANGUAGE and DOMAIN are what this file adds:
the audit measured the collections but never said which axis a repo belonged to, which is
how the Solidity-repos-are-the-code-corpus belief survived. See ``corpus/entries.py``.

See ``corpus/schema.py`` for what each field means and ``corpus/gate.py`` for the refusal.
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.entries_base import DAILY, WEEKLY, D, L, _d
from rugbrain.corpus.schema import (
    LICENCE_COPYLEFT,
    LICENCE_PERMISSIVE,
    LICENCE_PUBLIC_DOMAIN,
    LICENCE_UNLICENSED,
    CorpusDeclaration,
)

_SCAM: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "scam-intel-v2",
        "scam_intel_v2",
        cid="scam_intel_v2",
        language=L.DATA,
        domain=D.SCAM,
        url="internal://pipeline/scam-intel",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="107,878 pts. Largest scam corpus. Base 2560, twin is the 4096 serving lane.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "known-scams-blacklist",
        "known_scams",
        cid="known_scams",
        language=L.DATA,
        domain=D.SCAM,
        url="https://github.com/CryptoScamDB/blacklist",
        licence=LICENCE_UNLICENSED,
        cadence=DAILY,
        notes="26,720 pts, 29d STALE (audit §5.1) — the feed is dead. Unlicensed: "
        "usable internally, never product-cleared. Base 2560, twin is the 4096 lane.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "scam-patterns-mined",
        "scam_patterns",
        cid="scam_patterns",
        language=L.PYTHON,
        domain=D.SCAM,
        url="internal://pipeline/scam-patterns",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="7,102 pts.",
        observed=2560,
        retired=()
    ),
    _d(
        "scam-signatures",
        "scam_signatures",
        cid="scam_signatures",
        language=L.DATA,
        domain=D.SCAM,
        url="internal://pipeline/scam-signatures",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="0 points — declared, never fed.",
        observed=4096,
        retired=()
    ),
    _d(
        "scams-v2",
        "scams_v2",
        cid="scams_v2",
        language=L.DATA,
        domain=D.SCAM,
        url="https://github.com/scamsniffer/scam-database",
        licence=LICENCE_COPYLEFT,
        cadence=DAILY,
        notes="0 points. GPL-3.0 — standing risk to review, already held.",
        observed=2560,
    ),
    _d(
        "rug-events",
        "rug_events",
        cid="rug_events",
        language=L.DATA,
        domain=D.SCAM,
        url="internal://pipeline/rug-events",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="1,877 pts. THE one that matters (audit §1.2): the largest corpus "
        "with no 4096 twin, so a canonical-lane write is refused forever.",
        observed=2560,
    ),
    _d(
        "scanned-tokens",
        "scanned_tokens",
        cid="scanned_tokens",
        language=L.DATA,
        domain=D.SCAM,
        url="internal://pipeline/scanned-tokens",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="1 pt. 2560, no twin.",
        observed=2560,
    ),
    _d(
        "phishing-blacklist",
        "phishing_blacklist",
        cid="phishing_blacklist",
        language=L.DATA,
        domain=D.SCAM,
        url="https://github.com/MetaMask/eth-phishing-detect",
        licence=LICENCE_PUBLIC_DOMAIN,
        cadence=DAILY,
        notes="Declared target for the eth-phishing-destrolist feed. No Qdrant "
        "collection yet (lane_table: no collection, do not auto-create).",
    ),
    _d(
        "scam-database-sniffer",
        "scam_database",
        cid="scam_database",
        language=L.DATA,
        domain=D.SCAM,
        url="https://github.com/scamsniffer/scam-database",
        licence=LICENCE_COPYLEFT,
        cadence=DAILY,
        notes="Declared target for the Sniffer database feed. GPL-3.0.",
    ),
)

# ---------------------------------------------------------------------------
# WALLET LABELS

_WALLET: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "wallet-profiles",
        "wallet_profiles",
        cid="wallet_profiles",
        language=L.DATA,
        domain=D.WALLET_LABEL,
        url="internal://neo4j/wallet-labels",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="40,355 pts, the largest wallet-label corpus. Base 2560, twin is 4096.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "wallet-v2",
        "wallet_v2",
        cid="wallet_v2",
        language=L.DATA,
        domain=D.WALLET_LABEL,
        url="internal://neo4j/wallet-labels-v2",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="9,657 pts. Base 2560, twin is the 4096 serving lane.",
        observed=2560,
        retired=(2560,),
    ),
    _d(
        "wallet-clusters",
        "wallet_clusters",
        cid="wallet_clusters",
        language=L.DATA,
        domain=D.WALLET_LABEL,
        url="internal://pipeline/wallet-clusters",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="Pinned 1024 (retired lane). 0 points.",
        observed=1024,
        retired=()
    ),
)

__all__ = [
    "_SCAM",
    "_WALLET",
]
