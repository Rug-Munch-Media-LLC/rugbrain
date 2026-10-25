"""Declared publishers for the on-chain-analytics / wallet-visualisation corpus.

One declaration per publisher, so a refresh is a re-read of this table rather
than rediscovery by hand. Every ``llms``/``sitemap`` URL below returned HTTP 200
when this file was written (2026-10-25, Talos, ``RMI-RagBrain/0.1`` UA).

``status`` is a MEASUREMENT, not a wish:

  * ``live``    — discovery endpoint answered and pages are fetchable.
  * ``blocked`` — the publisher is declared but no content is reachable from
    this host (Cloudflare 403 / DNS failure). ``evidence`` records the exact
    observation so a later refresh can re-probe and flip it honestly, instead
    of the publisher silently vanishing from a corpus that claims coverage.

Licence is recorded per publisher because the fleet ruling is that the corpus is
INTERNAL and the operative test is *redistribution*, not use. Proprietary docs
are therefore usable here and explicitly not product-cleared — the string is
what a product-bound day greps for.

See ``analytics_docs.py`` for the fetcher and ``analytics_docs_pages.py`` for
the pure parsers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

LICENCE_PROPRIETARY: Final = "proprietary-docs (internal use only, no redistribution)"
LICENCE_OSS_DOCS: Final = "open-source-project-docs (upstream repo licence)"
LICENCE_ACADEMIC: Final = "academic-preprint (arXiv author licence, metadata CC0)"


@dataclass(frozen=True)
class Publisher:
    """One documentation publisher and how to enumerate it."""

    name: str
    base: str
    licence: str
    llms: str | None = None
    sitemap: str | None = None
    seeds: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    status: str = "live"
    evidence: str = ""


_OSS = LICENCE_OSS_DOCS
_PROP = LICENCE_PROPRIETARY

PUBLISHERS: Final[tuple[Publisher, ...]] = (
    Publisher(
        "etherscan",
        "https://docs.etherscan.io",
        _PROP,
        llms="https://docs.etherscan.io/llms.txt",
        seeds=("https://docs.etherscan.io/introduction.md",),
    ),
    Publisher(
        "blockscout",
        "https://docs.blockscout.com",
        _OSS,
        llms="https://docs.blockscout.com/llms.txt",
    ),
    Publisher(
        "nansen",
        "https://docs.nansen.ai",
        _PROP,
        llms="https://docs.nansen.ai/llms.txt",
        sitemap="https://docs.nansen.ai/sitemap.xml",
    ),
    Publisher(
        "metasleuth",
        "https://docs.metasleuth.io",
        _PROP,
        sitemap="https://docs.metasleuth.io/sitemap.xml",
    ),
    Publisher(
        "rotki",
        "https://docs.rotki.com",
        _OSS,
        llms="https://docs.rotki.com/llms.txt",
        sitemap="https://docs.rotki.com/sitemap.xml",
    ),
    Publisher(
        "solana-fm",
        "https://docs.solana.fm",
        _PROP,
        llms="https://docs.solana.fm/llms.txt",
        sitemap="https://docs.solana.fm/sitemap.xml",
    ),
    Publisher(
        "solscan",
        "https://docs.solscan.io",
        _PROP,
        sitemap="https://docs.solscan.io/sitemap.xml",
    ),
    Publisher(
        "cielo",
        "https://docs.cielo.finance",
        _PROP,
        llms="https://docs.cielo.finance/llms.txt",
        sitemap="https://docs.cielo.finance/sitemap.xml",
    ),
    Publisher(
        "zerion",
        "https://developers.zerion.io",
        _PROP,
        llms="https://developers.zerion.io/llms.txt",
        sitemap="https://developers.zerion.io/sitemap.xml",
    ),
    Publisher(
        "debank",
        "https://docs.cloud.debank.com",
        _PROP,
        llms="https://docs.cloud.debank.com/llms.txt",
        sitemap="https://docs.cloud.debank.com/sitemap.xml",
    ),
    Publisher(
        "bubblemaps",
        "https://docs.bubblemaps.io",
        _PROP,
        llms="https://docs.bubblemaps.io/llms.txt",
        sitemap="https://docs.bubblemaps.io/sitemap.xml",
        evidence="coordinate with corpus-bubblemaps workstream; dedupe on canonical_id",
    ),
    Publisher(
        "graphsense",
        "https://graphsense.info",
        LICENCE_ACADEMIC,
        sitemap="https://graphsense.info/sitemap.xml",
        seeds=("https://graphsense.info/",),
        evidence="docs.graphsense.info does not resolve; site docs only in this pass",
    ),
    Publisher(
        "breadcrumbs",
        "https://docs.breadcrumbs.app",
        _PROP,
        status="blocked",
        evidence="HTTP 403 for / (robots/bot block) — no auth bypass attempted",
    ),
    Publisher(
        "arkham",
        "https://arkm.com/docs",
        _PROP,
        status="blocked",
        evidence="docs.arkm.com DNS NXDOMAIN; arkm.com/docs + intel.arkm.com/api/docs HTTP 403",
    ),
    Publisher(
        "step-finance",
        "https://docs.step.finance",
        _PROP,
        status="blocked",
        evidence="docs.step.finance DNS NXDOMAIN on 2026-10-25",
    ),
    Publisher(
        "wallet-tracker",
        "https://docs.wallet-tracker.io",
        _PROP,
        status="blocked",
        evidence="docs.wallet-tracker.io + wallettracker.app DNS NXDOMAIN; canonical host unresolved",
    ),
)

#: arXiv Atom queries for the whitepaper half of the corpus: clustering,
#: entity resolution, fund tracing, label propagation. Fixed strings — an
#: unrecorded live query would make the corpus unreproducible.
ARXIV_QUERIES: Final[tuple[tuple[str, str], ...]] = (
    (
        "clustering",
        'all:"address clustering" AND all:blockchain',
    ),
    (
        "entity-resolution",
        'all:"entity resolution" AND all:cryptocurrency',
    ),
    (
        "fund-tracing",
        'all:"fund tracing" AND all:bitcoin',
    ),
    (
        "label-propagation",
        'all:"label propagation" AND all:blockchain',
    ),
)

ARXIV_LICENCE: Final = LICENCE_ACADEMIC
ARXIV_API: Final = "https://export.arxiv.org/api/query"

__all__ = [
    "ARXIV_API",
    "ARXIV_LICENCE",
    "ARXIV_QUERIES",
    "LICENCE_ACADEMIC",
    "LICENCE_OSS_DOCS",
    "LICENCE_PROPRIETARY",
    "PUBLISHERS",
    "Publisher",
]
