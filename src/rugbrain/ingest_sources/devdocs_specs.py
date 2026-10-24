"""The developer-documentation manifest: the one tuple every consumer reads.

THIS MODULE IS THE SINGLE SOURCE OF TRUTH for the devdocs ingest — with the declarations
themselves grouped in single-job modules and concatenated here, the same shape as
``corpus/entries.py``. ``devdocs.py`` fetches what this module exposes and nothing else;
``scripts/devdocs_manifest.py`` renders it to JSON/Markdown on demand (a VIEW, never a
second table). A second list anywhere would be a second divergent answer about what is in
the corpus and under which licence.

WHAT THIS COVERS, AND WHAT IT DELIBERATELY DOES NOT
    The ContextCrypto ``docs:`` lane carries the fleet's STACK docs (FastAPI, Pydantic,
    SQLAlchemy, Qdrant, Nuxt, LiteLLM, PTB, ...). This manifest carries the ADJACENT gap:
    the x402 payment protocol, the MCP protocol plus reference servers, the four
    languages the corpus is written in, and the engineering blogs whose design reasoning
    never lands in a spec. Overlap with that lane is a defect here, not a convenience —
    check ``docs/COVERAGE-AUDIT-2026-09-28.md`` before adding a family.

ADD ONE
    Append a :class:`~rugbrain.ingest_sources.devdocs_schema.DocSite` to the group module
    for its domain (``devdocs_specs_protocols``, ``devdocs_specs_languages``). Nothing
    else changes: the fetchers, the manifest renderer and the refresh script all read the
    tuples below. Prefer raw ``.md``/``.rst`` over HTML — a raw file arrives clean, whereas
    HTML must be stripped and the strip is lossy. Keep ``cap`` small; one page is already
    the whole unit of meaning.

Grouped so each file stays under the repo's 200-line cap and holds one job: a schema, a
protocol table, a language/blog table, and this aggregation.
"""

from __future__ import annotations

from typing import Final

from rugbrain.ingest_sources.devdocs_schema import (
    BLOG_COLLECTION,
    DOCS_COLLECTION,
    LICENCE_APACHE2,
    LICENCE_BSD3,
    LICENCE_CC_BY,
    LICENCE_GPL3,
    LICENCE_MIT,
    LICENCE_MIT_OR_APACHE2,
    LICENCE_PSF,
    DocSite,
)
from rugbrain.ingest_sources.devdocs_specs_languages import BLOG_SITES, LANGUAGE_SITES
from rugbrain.ingest_sources.devdocs_specs_protocols import PROTOCOL_SITES

#: Every declared family, in fetch order. The one tuple every consumer reads.
DOC_SITES: Final[tuple[DocSite, ...]] = PROTOCOL_SITES + LANGUAGE_SITES + BLOG_SITES

#: Non-RSS families (specs + language docs), fetched page by page by ``devdocs``.
STATIC_SITES: Final[tuple[DocSite, ...]] = PROTOCOL_SITES + LANGUAGE_SITES

#: RSS-backed families, one document per feed item, fetched by ``eng_blogs``.
FEED_SITES: Final[tuple[DocSite, ...]] = BLOG_SITES

__all__ = [
    "BLOG_COLLECTION",
    "BLOG_SITES",
    "DOCS_COLLECTION",
    "DOC_SITES",
    "FEED_SITES",
    "LANGUAGE_SITES",
    "LICENCE_APACHE2",
    "LICENCE_BSD3",
    "LICENCE_CC_BY",
    "LICENCE_GPL3",
    "LICENCE_MIT",
    "LICENCE_MIT_OR_APACHE2",
    "LICENCE_PSF",
    "PROTOCOL_SITES",
    "STATIC_SITES",
    "DocSite",
]
