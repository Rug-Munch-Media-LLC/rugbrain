"""Declared corpora, group: protocol reference.

Threat-intel and audit corpora live in ``entries_threat``. These five are the other
kind of reference material: protocol architecture, cross-chain and privacy design docs,
and the public-pattern index. Split out because design work rereads them constantly and
their cadences differ from the signal corpora, which are hourly.

``public_crypto_patterns`` is the notable row: 2 points, pinned to 2560 with no ``_8b``
twin, so it is stranded on a retired lane and a canonical write to it is refused forever
(audit §1.2, lane_table). ``l2_risk`` is already canonical at 4096 with no base to retire.

Every row is transcribed from ``docs/COVERAGE-AUDIT-2026-09-28.md`` §1. See
``corpus/schema.py`` for field meanings and ``corpus/gate.py`` for the refusal.
"""

from __future__ import annotations

from typing import Final

from rugbrain.corpus.entries_base import DAILY, MONTHLY, WEEKLY, D, L, _d
from rugbrain.corpus.schema import (
    LICENCE_PERMISSIVE,
    CorpusDeclaration,
)

_PROTOCOL: Final[tuple[CorpusDeclaration, ...]] = (
    _d(
        "cross-chain-intel",
        "cross_chain",
        cid="cross_chain",
        language=L.DATA,
        domain=D.THREAT_INTEL,
        url="internal://pipe/xchain",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        notes="13 pts at 2560. Twin exists but NOT owned (lane_table).",
        observed=2560,
        retired=()
    ),
    _d(
        "protocol-design-docs",
        "protocol_design",
        cid="protocol_design",
        language=L.MARKDOWN,
        domain=D.THREAT_INTEL,
        url="internal://rag/proto",
        licence=LICENCE_PERMISSIVE,
        cadence=MONTHLY,
        notes="58 pts at 2560. Twin exists but NOT owned (lane_table).",
        observed=2560,
    ),
    _d(
        "public-crypto-patterns",
        "public_crypto_patterns",
        cid="public_crypto_patterns",
        language=L.SOLIDITY,
        domain=D.THREAT_INTEL,
        url="https://modelcontextprotocol/registry",
        licence=LICENCE_PERMISSIVE,
        cadence=WEEKLY,
        notes="2 pts. Pinned 2560, no twin (lane_table) — stranded.",
        observed=2560,
    ),
    _d(
        "privacy-protocols",
        "privacy_protocol",
        cid="privacy_protocol",
        language=L.RUST,
        domain=D.THREAT_INTEL,
        url="internal://rag/privacy",
        licence=LICENCE_PERMISSIVE,
        cadence=MONTHLY,
        notes="4 pts. Already canonical, no base to retire.",
        observed=4096,
        retired=()
    ),
    _d(
        "l2-risk",
        "l2_risk",
        cid="l2_risk",
        language=L.DATA,
        domain=D.THREAT_INTEL,
        url="internal://pipe/l2",
        licence=LICENCE_PERMISSIVE,
        cadence=DAILY,
        observed=4096,
        retired=()
    ),
)

__all__ = [
    "_PROTOCOL",
]
