"""Declared devdocs, group: social protocols DegenFeed federates with.

DegenFeed reads and writes Farcaster casts, AT Protocol records (Bluesky) and Mastodon
statuses, so the mechanics an engineer needs are identity/signing, the validation a hub
or PDS applies to a record, and the delivery surface (streaming vs webhook). Those are
exactly the pages declared here.

EVERY URL IS PINNED TO A COMMIT SHA, not ``main``: the provenance a retrieved chunk
carries must name the tree the text came from, or a "why does the doc disagree with the
live API" investigation has nothing to check against. The SHA was resolved with
``git ls-remote`` and every path below was HTTP-200 verified before it was declared.

LICENCE IS THE WORK'S, NOT THE SITE'S. Farcaster's spec repo (``farcasterxyz/protocol``)
publishes no LICENSE file, so it is NOT declared — unverifiable terms are not "clean".
``bluesky-social/lexicon`` has no resolvable HEAD and is likewise absent.

See ``devdocs_schema`` for the field contract, ``devdocs`` for the fetcher, and
``devdocs_specs`` for the aggregate every consumer reads.
"""

from __future__ import annotations

from typing import Final

from rugbrain.ingest_sources.devdocs_schema import (
    DOCS_COLLECTION,
    LICENCE_GFDL13,
    LICENCE_MIT,
    LICENCE_MIT_OR_APACHE2,
    DocSite,
)

_FC_SHA: Final[str] = "3d19a20654c4223546c7f25b8a1c43211fde3d6d"
_AT_SHA: Final[str] = "57c85d5ea03583dae3ae8d1f51106210dcad315b"
_MD_SHA: Final[str] = "f9e4c0116b17d28b56db63798662f1bc3aa15420"
_LENS_SHA: Final[str] = "d4c662aac780e669cd23769730cdc4f0189ee5be"


def _pin(repo: str, ref: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    """Absolute, commit-pinned raw URLs — one document per declared path."""
    return tuple(
        f"https://raw.githubusercontent.com/{repo}/{ref}/{path}" for path in paths
    )


SOCIAL_SITES: Final[tuple[DocSite, ...]] = (
    DocSite(
        name="farcaster_hub",
        family="social",
        url="https://github.com/farcasterxyz/hub-monorepo",
        licence=LICENCE_MIT,
        doc_type="protocol_spec",
        collection=DOCS_COLLECTION,
        urls=_pin(
            "farcasterxyz/hub-monorepo",
            _FC_SHA,
            ("README.md", "packages/core/README.md", "packages/hub-web/README.md"),
        ),
    ),
    DocSite(
        name="atproto",
        family="social",
        url="https://github.com/bluesky-social/atproto",
        licence=LICENCE_MIT_OR_APACHE2,
        doc_type="protocol_spec",
        collection=DOCS_COLLECTION,
        urls=_pin(
            "bluesky-social/atproto",
            _AT_SHA,
            (
                "README.md",
                "packages/pds/README.md",
                "packages/repo/README.md",
                "packages/lexicon/README.md",
                "packages/identity/README.md",
                "packages/xrpc/README.md",
                "packages/sync/README.md",
                "packages/api/README.md",
            ),
        ),
    ),
    DocSite(
        name="mastodon_docs",
        family="social",
        url="https://github.com/mastodon/documentation",
        licence=LICENCE_GFDL13,
        doc_type="api_reference",
        collection=DOCS_COLLECTION,
        urls=_pin(
            "mastodon/documentation",
            _MD_SHA,
            (
                "content/en/api/rate-limits.md",
                "content/en/api/guidelines.md",
                "content/en/admin/webhooks.md",
                "content/en/methods/statuses.md",
                "content/en/spec/activitypub.md",
            ),
        ),
    ),
    DocSite(
        name="lens_core",
        family="social",
        url="https://github.com/lens-protocol/core",
        licence=LICENCE_MIT,
        doc_type="protocol_spec",
        collection=DOCS_COLLECTION,
        urls=_pin("lens-protocol/core", _LENS_SHA, ("README.md",)),
    ),
)
