"""Declared devdocs, group: language references and engineering blogs.

Two halves, both thin in the corpus today.

LANGUAGE REFERENCE — the official docs for the four languages the corpus is written in
(Python, Rust, Go, Solidity). Not tutorials site-wide: the pages that answer "what does
this construct guarantee", which is the question a generated patch gets wrong. Raw
``.md``/``.rst`` is preferred wherever the project publishes it, because a raw file
arrives clean and HTML must be stripped lossily.

ENGINEERING BLOGS — the design reasoning that never lands in a spec: Flashbots on PBS
ordering, Paradigm on MEV and AMM design, Base on L2 sequencing, Solana on runtime
constraints, a16z crypto on mechanism design. ``research_v2`` held 158 points against a
2.13M code corpus, so the marginal document here is worth more than the marginal
Solidity file.

See ``devdocs_schema`` for the field contract and ``devdocs_specs`` for the aggregate.
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
    LICENCE_MIT_OR_APACHE2,
    LICENCE_PSF,
    DocSite,
)

_LANGUAGES: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="python_docs",
        family="python",
        url="https://docs.python.org/3/",
        licence=LICENCE_PSF,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://docs.python.org/3/library/asyncio-task.html",
            "https://docs.python.org/3/library/typing.html",
            "https://docs.python.org/3/library/contextvars.html",
        ),
        cap=3,
    ),
    DocSite(
        name="rust_reference",
        family="rust",
        url="https://doc.rust-lang.org/reference/",
        licence=LICENCE_MIT_OR_APACHE2,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://raw.githubusercontent.com/rust-lang/reference/master/src/items/functions.md",
            "https://raw.githubusercontent.com/rust-lang/reference/master/src/types/closure.md",
            "https://raw.githubusercontent.com/rust-lang/reference/master/src/memory-model.md",
        ),
        cap=3,
    ),
    DocSite(
        name="go_docs",
        family="go",
        url="https://go.dev/doc/",
        licence=LICENCE_BSD3,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://go.dev/doc/effective_go",
            "https://go.dev/ref/spec",
            "https://go.dev/doc/faq",
        ),
        cap=3,
    ),
    DocSite(
        name="solidity_docs",
        family="solidity",
        url="https://docs.soliditylang.org/",
        licence=LICENCE_GPL3,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://docs.soliditylang.org/en/latest/security-considerations.html",
            "https://docs.soliditylang.org/en/latest/contracts.html",
        ),
        cap=2,
    ),
)

#: Engineering blogs whose design reasoning is not in any spec. RSS, one doc per item.
_BLOGS: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="solana_engineering",
        family="solana",
        url="https://solana.com/news",
        licence=LICENCE_APACHE2,
        doc_type="engineering_blog",
        collection=BLOG_COLLECTION,
        urls=("https://solana.com/news/rss.xml",),
        cap=8,
    ),
    DocSite(
        name="base_engineering",
        family="base",
        url="https://blog.base.org",
        licence=LICENCE_CC_BY,
        doc_type="engineering_blog",
        collection=BLOG_COLLECTION,
        urls=("https://blog.base.org/rss.xml",),
        cap=8,
    ),
    DocSite(
        name="flashbots_writings",
        family="flashbots",
        url="https://writings.flashbots.net",
        licence=LICENCE_CC_BY,
        doc_type="engineering_blog",
        collection=BLOG_COLLECTION,
        urls=("https://writings.flashbots.net/rss.xml",),
        cap=8,
    ),
    DocSite(
        name="paradigm_research",
        family="paradigm",
        url="https://www.paradigm.xyz/writing",
        licence=LICENCE_CC_BY,
        doc_type="engineering_blog",
        collection=BLOG_COLLECTION,
        # Paradigm serves NO RSS feed (probed 2026-10-25: /feed.xml, /rss, /feed and
        # /writing/rss.xml all 404). The writing index is declared so the family is not
        # silently missing; a refresh is EXPECTED to report it thin until a JS-render
        # path exists, and that expectation is visible in the run report rather than
        # hidden behind a source that pretends to exist.
        urls=("https://www.paradigm.xyz/writing",),
        cap=8,
    ),
    DocSite(
        name="a16zcrypto_research",
        family="a16z-crypto",
        url="https://a16zcrypto.com/posts/",
        licence=LICENCE_CC_BY,
        doc_type="engineering_blog",
        collection=BLOG_COLLECTION,
        urls=("https://a16zcrypto.com/feed/",),
        cap=8,
    ),
)

#: Language reference pages, in fetch order.
LANGUAGE_SITES: Final[tuple[DocSite, ...]] = _LANGUAGES

#: RSS-backed families, one document per item.
BLOG_SITES: Final[tuple[DocSite, ...]] = _BLOGS

__all__ = ["BLOG_SITES", "LANGUAGE_SITES"]
