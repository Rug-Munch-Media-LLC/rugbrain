"""Declared devdocs, group: the six languages the fleet writes but the corpus lacked.

``devdocs_specs_languages`` carries Python, Rust, Go and Solidity. This module carries the
rest of the real surface — TypeScript (frontend + tooling), Move on Sui and Aptos, Cairo,
Solana/Anchor (Rust-derived, but a different account model and a different failure set),
Bash (every script we ship) and Vyper. The gap was not cosmetic: a generated patch in any
of these had no authoritative page behind it, so it fell back to model memory, which is
where the wrong attribute name or wrong builtin comes from.

WHY THESE PAGES AND NOT A TUTORIAL SERIES. Each URL answers "what does this construct
guarantee, and where does it bite" — ownership/abilities/resources, generics and
narrowing, calldata vs storage, shell expansion. Prose tutorials restate syntax an
embedder already knows.

RAW FIRST. Move and Vyper publish their books as reStructuredText/Markdown in-repo, so
they arrive clean; the others are HTML and are stripped by ``devdocs_page`` (lossy, but
bounded, and the code blocks survive because they are plain text inside ``<pre>``).

PROBE 2026-10-25: every URL below returned 200 with a usable payload. Sui/Aptos/Anchor
paths under ``main`` move often, so a ``refresh_devdocs`` failure line is the early
warning, not an emergency.

See ``devdocs_schema`` for the field contract and ``devdocs_specs`` for the aggregate.
"""

from __future__ import annotations

from typing import Final

from rugbrain.ingest_sources.devdocs_schema import (
    DOCS_COLLECTION,
    LICENCE_APACHE2,
    LICENCE_GFDL13,
    LICENCE_MIT,
    DocSite,
)

_RAW = "https://raw.githubusercontent.com"

_EXTRA_LANGUAGES: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="typescript_docs",
        family="typescript",
        url="https://www.typescriptlang.org/docs/handbook/",
        licence=LICENCE_APACHE2,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://www.typescriptlang.org/docs/handbook/2/narrowing.html",
            "https://www.typescriptlang.org/docs/handbook/2/generics.html",
            "https://www.typescriptlang.org/docs/handbook/2/types-from-types.html",
        ),
        cap=3,
    ),
    DocSite(
        name="move_language",
        family="move",
        url="https://move-language.github.io/move/",
        licence=LICENCE_APACHE2,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            f"{_RAW}/move-language/move/main/language/documentation/book/src/abilities.md",
            "https://docs.sui.io/concepts/sui-move-concepts",
            "https://aptos.dev/en/build/smart-contracts/book",
        ),
        cap=3,
    ),
    DocSite(
        name="cairo_book",
        family="cairo",
        url="https://book.cairo-lang.org/",
        licence=LICENCE_MIT,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=("https://book.cairo-lang.org/",),
        cap=1,
    ),
    DocSite(
        name="anchor_solana",
        family="solana",
        url="https://www.anchor-lang.com/docs",
        licence=LICENCE_APACHE2,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://www.anchor-lang.com/docs",
            "https://solana.com/docs/core/programs",
            "https://solana.com/docs/programs/anchor",
        ),
        cap=3,
    ),
    DocSite(
        name="bash_manual",
        family="bash",
        url="https://www.gnu.org/software/bash/manual/bash.html",
        licence=LICENCE_GFDL13,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://www.gnu.org/software/bash/manual/html_node/Shell-Parameter-Expansion.html",
            "https://www.gnu.org/software/bash/manual/html_node/Shell-Builtin-Commands.html",
        ),
        cap=2,
    ),
    DocSite(
        name="vyper_docs",
        family="vyper",
        url="https://docs.vyperlang.org/en/stable/",
        licence=LICENCE_APACHE2,
        doc_type="language_docs",
        collection=DOCS_COLLECTION,
        urls=(
            "https://docs.vyperlang.org/en/stable/control-structures.html",
            "https://docs.vyperlang.org/en/stable/types.html",
            f"{_RAW}/vyperlang/vyper/master/docs/index.rst",
        ),
        cap=3,
    ),
)

#: Additional language references, in fetch order.
LANGUAGE_EXTRA_SITES: Final[tuple[DocSite, ...]] = _EXTRA_LANGUAGES

__all__ = ["LANGUAGE_EXTRA_SITES"]
