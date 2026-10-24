"""The devdocs manifest SCHEMA: what a declared documentation source is.

One job — define :class:`DocSite` and the collection/licence vocabulary it uses. The
declarations themselves live in ``devdocs_specs_protocols`` (x402, MCP) and
``devdocs_specs_languages`` (Python/Rust/Go/Solidity, engineering blogs), aggregated by
``devdocs_specs``. Split this way because the declaration set does not fit the repo's
200-line cap in one file, and because a schema is not a table of contents.

WHY THE COLLECTIONS ARE REUSED, NOT INVENTED
    These are the live 4096 (``_8b``) write lanes of the baseline knowledge collections.
    Writing devdocs into a NEW collection would fork the retrieval lane for no gain and
    duplicate points already reachable by metadata filter. The ``_8b`` suffix is
    deliberate: the base names (``rag_documents`` at 667K, ``research_v2`` at 158) are the
    RETIRED 2560 generations, so a 4096 write there is refused by the dimension guard —
    which is the correct outcome, not a bug to route around. ``docs/DUAL_TIER_RAG.md``
    owns that split; this module never re-derives it.

WHY LICENCES ARE RECORDED
    Every entry carries the licence of the WORK, not of the site. The corpus is internal
    and nothing is sold, so the test is REDISTRIBUTION: copyleft and non-commercial
    sources are usable internally and stay identifiable the day one of them feeds a
    shipped product. Tokens match ``corpus/schema_vocab`` spelling so a product gate can
    evaluate them without translation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

DOCS_COLLECTION: Final[str] = "rag_documents_8b"
BLOG_COLLECTION: Final[str] = "research_v2_8b"

LICENCE_APACHE2: Final[str] = "Apache-2.0"
LICENCE_MIT: Final[str] = "MIT"
LICENCE_MIT_OR_APACHE2: Final[str] = "MIT OR Apache-2.0"
LICENCE_PSF: Final[str] = "PSF-2.0"
LICENCE_BSD3: Final[str] = "BSD-3-Clause"
LICENCE_GPL3: Final[str] = "GPL-3.0-only"
LICENCE_CC_BY: Final[str] = "CC-BY-4.0"


@dataclass(frozen=True, slots=True)
class DocSite:
    """One documentation family: identity, provenance, licence, and pages to fetch.

    ``urls`` are ABSOLUTE and canonical. Each yields exactly one document, so the count
    of a bounded run is predictable rather than discovered. ``url`` is the human-facing
    provenance anchor (site or repo root), recorded on every document emitted.
    """

    name: str
    family: str
    url: str
    licence: str
    doc_type: str
    collection: str
    urls: tuple[str, ...]
    cap: int = 6


__all__ = [
    "BLOG_COLLECTION",
    "DOCS_COLLECTION",
    "LICENCE_APACHE2",
    "LICENCE_BSD3",
    "LICENCE_CC_BY",
    "LICENCE_GPL3",
    "LICENCE_MIT",
    "LICENCE_MIT_OR_APACHE2",
    "LICENCE_PSF",
    "DocSite",
]
