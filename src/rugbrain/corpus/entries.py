"""The declared corpora: every collection in the fleet, categorised and dimensioned.

THIS IS THE SINGLE SOURCE OF TRUTH, not a report. ``scripts/corpus_manifest.py``
imports this; the embed gate imports this; the undeclared-collection check compares
live Qdrant against this. A second table anywhere would be a second divergent answer,
which is how this fleet ended up with four live dimensions at once.

HOW TO ADD ONE
    Append one :class:`~rugbrain.corpus.schema.CorpusDeclaration` to the group module
    for its domain — ``entries_code``, ``entries_threat``, ``entries_protocol``,
    ``entries_scam``, ``entries_ops``, ``entries_knowledge``, ``entries_runtime``.
    Nothing else in the repo changes: the gate, the audit script and the mismatch
    report all read :data:`DECLARED` below, which is the concatenation of those
    groups. That is the whole add path.

Grouped by domain, and split into several modules per domain, because the full
declaration set does not fit under the repo's 200-line cap in one file.
"""

from __future__ import annotations

from rugbrain.corpus.entries_base import (
    DAILY,
    HOURLY,
    MONTHLY,
    ON_DEMAND,
    PROBE,
    RUNTIME,
    UNKNOWN_CADENCE,
    WEEKLY,
)
from rugbrain.corpus.entries_code import _CODE
from rugbrain.corpus.entries_exploit import _EXPLOIT
from rugbrain.corpus.entries_knowledge import _KNOWLEDGE
from rugbrain.corpus.entries_ops import _NEWS_MARKET
from rugbrain.corpus.entries_protocol import _PROTOCOL
from rugbrain.corpus.entries_runtime import _NON_CORPUS
from rugbrain.corpus.entries_scam import _SCAM, _WALLET
from rugbrain.corpus.entries_threat import _THREAT
from rugbrain.corpus.schema import CorpusDeclaration

#: EVERY declaration, in group order. The one tuple every consumer reads.
DECLARED: tuple[CorpusDeclaration, ...] = (
    _CODE
    + _EXPLOIT
    + _THREAT
    + _SCAM
    + _WALLET
    + _NEWS_MARKET
    + _KNOWLEDGE
    + _PROTOCOL
    + _NON_CORPUS
)

__all__ = [
    "DAILY",
    "DECLARED",
    "HOURLY",
    "MONTHLY",
    "ON_DEMAND",
    "PROBE",
    "RUNTIME",
    "UNKNOWN_CADENCE",
    "WEEKLY",
]
