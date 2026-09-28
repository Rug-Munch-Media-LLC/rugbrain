"""Corpus manifest: every corpus declares its category and dim BEFORE it embeds.

The operator's ask — "all repos we grab categorize before embed so there is no
confusion sorting nonsense" and "never again a second of dim confusion" — is
enforced here, structurally, not by convention:

  * ``schema.CorpusDeclaration``  — the only shape that may be embedded. Language
    and domain are closed vocabularies, so a corpus cannot be uncategorised.
  * ``entries.DECLARED``          — the single source of truth: 69 declarations
    covering every live collection, transcribed from
    ``docs/COVERAGE-AUDIT-2026-09-28.md``. Other modules import this; there is no
    second table.
  * ``gate.require_declared``     — the refusal. A corpus with no declared dim, or
    on a retired lane, or a ``code`` corpus with no real language, RAISES before
    the embedder is called. No warning path, no env var to disable it.
  * ``verify.verify_live_widths`` — compares each declared dim against the LIVE
    Qdrant width (``lane_dims.fetch_live_dim``) and reports every live collection
    absent from the manifest. GET only. A name whose live width matches a declared
    ``retired_dims`` entry is retention, not a mismatch.

The base↔``_8b`` relationship is not reimplemented: it is Lane I's
``qdrant_store.lane_resolver``, and the dims come from
``qdrant_store.lane_table``. This package adds the DECLARATION those modules
assumed existed.

Add a corpus by appending one ``_d(...)`` to the group module for its domain. That
is the entire add path.
"""

from __future__ import annotations

from rugbrain.corpus.entries import DECLARED
from rugbrain.corpus.gate import (
    UndeclaredCorpus,
    declared_corpus,
    require_declared,
)
from rugbrain.corpus.schema import (
    MEASURED_ON,
    UNCLASSIFIED,
    CorpusDeclaration,
    CorpusDomain,
    CorpusLanguage,
    LicenceStatus,
)
from rugbrain.corpus.verify import DimMismatch, VerificationReport, verify_live_widths

__all__ = [
    "DECLARED",
    "MEASURED_ON",
    "UNCLASSIFIED",
    "CorpusDeclaration",
    "CorpusDomain",
    "CorpusLanguage",
    "DimMismatch",
    "LicenceStatus",
    "UndeclaredCorpus",
    "VerificationReport",
    "declared_corpus",
    "require_declared",
    "verify_live_widths",
]
