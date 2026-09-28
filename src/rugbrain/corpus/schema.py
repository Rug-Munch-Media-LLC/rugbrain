"""The corpus manifest schema: what every corpus must declare BEFORE it embeds.

THE POINT
    The fleet has had four vector dimensions live at once (1024 retired 2026-09-14,
    2048 nemotron, 2560 the 4B generation, 4096 canonical) and has discovered a
    mismatch only when the store's dimension guard refuses a write. That is the
    wrong place to find out: the guard is a symptom, and a guard that fires late
    has already burned an embed pass. A corpus that has not DECLARED its language,
    domain, collection, dim, licence and cadence is not yet a corpus — it is a
    pile of documents that will be embedded at whatever dim the caller happened to
    have, into whatever collection the caller happened to name.

    So the declaration is the precondition, and this module is where that is
    expressed. :class:`CorpusDeclaration` is the only shape that may be embedded.
    Missing a field is not a warning; :mod:`rugbrain.corpus.gate` raises.

THE FIELDS, and why each one exists
    name, id         — identity. ``id`` is stable across renames of ``name`` so a
                       declaration can be retired without orphaning its points.
    language, domain — categorisation. These are the two axes the operator asked
                       for ("categorize before embed so there is no confusion
                       sorting nonsense"). They are closed vocabularies, not free
                       text, precisely so an uncategorised corpus cannot be
                       declared at all.
    collection       — the write target, named as the OWNER declares it (a base
                       name). Resolution to the ``_8b`` twin is Lane I's
                       ``resolve_collection``; this module never re-derives it.
    dim              — the declared width. May be ``None`` ONLY for a collection
                       that genuinely does not exist in Qdrant yet; the gate
                       refuses that case for a corpus about to be embedded.
    source_url, licence, refresh_cadence, last_verified
                   — provenance. The corpus is INTERNAL and PRIVATE and nothing
                       is sold, so the licence test is REDISTRIBUTION, not use:
                       no-licence, CC-BY-NC and gated-but-token-accessible sources
                       are all usable. The licence is still recorded on every entry
                       because the day one of these feeds a shipped product, the
                       ones never cleared for that need to be identifiable.
    last_verified    — when a human or a verifier last confirmed the declaration
                       against reality. Its absence is the finding in
                       ``docs/COVERAGE-AUDIT-2026-09-28.md`` §5.2: 48 of 61
                       collections carry no ingest-side timestamp at all, so for
                       4.26M points (67% of the fleet) nobody can say when the
                       data was last written.

See ``corpus/entries.py`` for the declared set and ``corpus/gate.py`` for the
refusal that is enforced rather than logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from rugbrain.corpus.schema_vocab import (
    CODE_LANGUAGES,
    LICENCE_COPYLEFT,
    LICENCE_GATED,
    LICENCE_NOASSERTION,
    LICENCE_NON_COMMERCIAL,
    LICENCE_PERMISSIVE,
    LICENCE_PUBLIC_DOMAIN,
    LICENCE_UNLICENSED,
    REDISTRIBUTABLE_LICENCES,
    UNCLASSIFIED,
    CorpusDomain,
    CorpusLanguage,
    LicenceStatus,
)

#: Epoch day the declared manifest was measured against live Qdrant. Kept as a
#: constant rather than a default so a stale manifest is visibly stale.
MEASURED_ON: Final[str] = "2026-09-28"


@dataclass(frozen=True, slots=True)
class CorpusDeclaration:
    """One corpus, fully declared. Frozen: a declaration is a fact, not a knob.

    ``collection`` is the name as the OWNER declares it. A migrated base name
    (``threat_intel_v2``) stays a base name here; resolving it to its ``_8b``
    twin is ``lane_resolver.resolve_collection``'s job and is never re-derived,
    so there is exactly one answer to "which collection does this write to".

    ``dim`` is the dim this corpus is EMBEDDED AT — the write lane. It is
    deliberately allowed to be ``None`` so an unbuilt collection can be declared
    honestly; the gate refuses that when the corpus is about to be embedded, which
    is where an absent dim becomes a wrong vector.

    ``observed_live_dim`` is a different fact and must not be conflated with it.
    A migrated corpus is declared at 4096 (where writes go) while the base
    collection it owns still sits at 2560 (where the old points are); that is the
    no-overwrite retention, not a contradiction. Writing one field for both is
    exactly how a 2560/4096 split gets discovered by the dimension guard instead
    of by a manifest. ``verify`` compares the two and only reports a genuine
    disagreement.
    """

    name: str
    id: str
    language: CorpusLanguage
    domain: CorpusDomain
    collection: str
    dim: int | None
    source_url: str
    licence: str
    refresh_cadence: str
    last_verified: str | None = None
    notes: str = ""
    #: The width Qdrant actually has for this collection, as measured on
    #: MEASURED_ON. This is the DIMENSION the name was created at, which is
    #: deliberately NOT the same fact as :attr:`dim` — see below.
    observed_live_dim: int | None = None
    #: Historical width(s) this OWNED NAME has itself lived at. Read-only
    #: generations; a write lane never targets one. Recorded so a mismatch
    #: against the live width is explainable rather than mysterious.
    #:
    #: A tuple entry means "this name really was created at that width", so
    #: ``verify`` may treat a live width found here as documented no-overwrite
    #: retention. It is deliberately NOT the width of a ``_8b`` twin: the twin
    #: is a different collection with its own declaration, and listing its width
    #: here would mask exactly the stranded-lane defect that has no twin to hold
    #: the canonical points.
    retired_dims: tuple[int, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def corpus_key(self) -> str:
        """The stable identity used everywhere for lookup and reporting."""
        return self.id

    @property
    def is_fully_declared(self) -> bool:
        """True when nothing required to embed is missing.

        A ``None`` dim counts as incomplete by design: the whole operator ask is
        that a corpus is categorised and dimensioned before it embeds, and a
        corpus with no declared dim is the one case the embed path must refuse.
        """
        return bool(
            self.name
            and self.id
            and self.language
            and self.domain
            and self.collection
            and self.dim
            and self.source_url
            and self.licence
            and self.refresh_cadence
        )

    @property
    def licence_status(self) -> LicenceStatus:
        """What the declared licence permits. See :data:`REDISTRIBUTABLE_LICENCES`."""
        token = self.licence.strip().lower()
        if token in REDISTRIBUTABLE_LICENCES:
            return LicenceStatus.REDISTRIBUTABLE
        if "gated" in token or "registration" in token:
            return LicenceStatus.GATED_ACCESS
        if "noassertion" in token:
            return LicenceStatus.UNKNOWN
        if not token or "no-licence" in token or "unlicensed" in token:
            return LicenceStatus.INTERNAL_ONLY
        if "non-commercial" in token or "nc" in token or "cc-by-nc" in token:
            return LicenceStatus.INTERNAL_ONLY
        return LicenceStatus.INTERNAL_ONLY

    def declared_for_redistribution(self) -> bool:
        """True when the licence clears this corpus for a shipped product.

        False for every copyleft, non-commercial, unlicensed, gated or unknown
        source. This is the predicate a product-bound day evaluates; the
        internal corpus is unaffected, nothing here gates a write.
        """
        return self.licence_status is LicenceStatus.REDISTRIBUTABLE


__all__ = [
    "CODE_LANGUAGES",
    "LICENCE_COPYLEFT",
    "LICENCE_GATED",
    "LICENCE_NOASSERTION",
    "LICENCE_NON_COMMERCIAL",
    "LICENCE_PERMISSIVE",
    "LICENCE_PUBLIC_DOMAIN",
    "LICENCE_UNLICENSED",
    "REDISTRIBUTABLE_LICENCES",
    "UNCLASSIFIED",
    "CorpusDomain",
    "CorpusLanguage",
    "LicenceStatus",
    "MEASURED_ON",
    "CorpusDeclaration",
]
