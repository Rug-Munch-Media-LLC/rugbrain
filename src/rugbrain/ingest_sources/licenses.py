"""Per-source licence and permitted-use registry.

Every ingest source declares here what may legally be done with its data, and
the ingest path reads it rather than trusting a comment. A licence found in a
docstring is a claim; this is a gate.

The default is the SAFEST permitted use — ``research_only``, not chunkable, not
redistributable — so a new source is protected until someone explicitly widens
it. That is deliberate: the failure mode we are defending against is a source
being ingested, quietly chunked, and then surfaced through a commercial
endpoint, which is how a research tool becomes a licensing problem.

The one real case that motivated this: OpenSanctions' default distribution is
**CC BY-NC-ND** (NonCommercial + NoDerivatives). Its own site states "data is
free for non-commercial use". Two consequences this registry encodes:

* **NoDerivatives** — the record must be stored verbatim. Any templating,
  summarising or rewriting is a derivative work, so this source is not chunked
  and its `content` is the source text.
* **NonCommercial** — internal research and screening are fine; redistribution
  and anything reachable from a paid product is not.

Public-domain replacements are preferred where one exists. OFAC's SDN list is
a US Government work and carries no such restriction, so it is the sanctioned
source of record for "is this address sanctioned" and needs no licence carve-out.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Permitted-use levels, ordered from safest to widest.
RESEARCH_ONLY = "research_only"
INTERNAL = "internal"
PUBLIC = "public"


@dataclass(frozen=True)
class SourceTerms:
    """What may legally be done with one source's records."""

    source_id: str
    license: str
    permitted_use: str = RESEARCH_ONLY
    #: May the record be chunked/summarised? False for NoDerivatives sources.
    chunkable: bool = False
    #: May the raw records be redistributed? False for NonCommercial sources.
    redistribute: bool = False
    note: str = ""

    def payload_fields(self) -> dict[str, object]:
        """Fields to stamp on every stored point, for audit."""
        return {
            "source_id": self.source_id,
            "license": self.license,
            "permitted_use": self.permitted_use,
            "chunkable": self.chunkable,
            "redistribute": self.redistribute,
        }


#: Declared terms. Anything absent falls back to the safest default.
TERMS: dict[str, SourceTerms] = {
    "opensanctions": SourceTerms(
        source_id="opensanctions",
        license="CC BY-NC-ND 4.0",
        permitted_use=RESEARCH_ONLY,
        chunkable=False,
        redistribute=False,
        note=(
            "NonCommercial + NoDerivatives. Store verbatim, never chunk or "
            "rewrite. Research/screening only; must not be surfaced from a "
            "commercial endpoint or redistributed. Prefer OFAC SDN (public "
            "domain) where only a sanctions answer is needed."
        ),
    ),
    "ofac_sdn": SourceTerms(
        source_id="ofac_sdn",
        license="US Government work - public domain",
        permitted_use=INTERNAL,
        chunkable=True,
        redistribute=True,
        note="US Government work carries no copyright. Safe default for sanctions lookups.",
    ),
    # 2026-09-28: the two verified rug datasets, added because
    # ``rug_events`` derives structured rows from both and the registry is the
    # gate that says whether that derivation is permitted.
    "scythe_radar": SourceTerms(
        source_id="scythe_radar",
        license="CC0-1.0",
        permitted_use=INTERNAL,
        chunkable=True,
        redistribute=True,
        note=(
            "CC0-1.0 public-domain dedication: no rights reserved, so deriving "
            "structured rug_events rows and redistributing them are both fine. "
            "The archive itself is bilingual (zh+en)."
        ),
    ),
    "deflabx_solrpds": SourceTerms(
        source_id="deflabx_solrpds",
        license="see source_url - derivative measurements, no licence declared",
        permitted_use=INTERNAL,
        chunkable=True,
        redistribute=False,
        note=(
            "On-chain liquidity measurements already ingested into this repo's "
            "own defi_exploits/exploits collections. Projecting them into "
            "structured rug_events rows is re-organisation of data we already "
            "hold, not redistribution of the upstream project."
        ),
    ),
    "crpwarner": SourceTerms(
        source_id="crpwarner",
        license="UNDECLARED - no SPDX licence in the repository",
        permitted_use=RESEARCH_ONLY,
        chunkable=False,
        redistribute=False,
        note=(
            "USENIX Sec'22 academic artifact with no declared licence. Stored "
            "VERBATIM: never templated, rewritten or summarised. Research use "
            "only. Gated in crpwarner_rug_events.py on "
            "ALLOW_UNLICENSED_CORPORA=1; lead decision still open."
        ),
    ),
}


def terms_for(source_id: str) -> SourceTerms:
    """Declared terms for a source, or the safest default when undeclared."""
    return TERMS.get(source_id) or SourceTerms(
        source_id=source_id,
        license="UNDECLARED",
        note="No terms declared; treated as research-only and un-chunkable until reviewed.",
    )


def may_chunk(source_id: str) -> bool:
    """Whether records from this source may be chunked or rewritten."""
    return terms_for(source_id).chunkable


def may_redistribute(source_id: str) -> bool:
    """Whether raw records from this source may be redistributed."""
    return terms_for(source_id).redistribute


def is_research_only(source_id: str) -> bool:
    """Whether the source is restricted to internal research use."""
    return terms_for(source_id).permitted_use == RESEARCH_ONLY
