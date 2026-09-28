"""Shared fixtures for the corpus-manifest tests.

Split from ``test_corpus_manifest.py`` so both halves stay under the repo's
200-line cap without duplicating the declaration builder or the Qdrant stand-in.
Two copies of ``_decl`` would be two answers to "what is a complete declaration",
which is the failure this package exists to remove.
"""

from __future__ import annotations

import httpx

from rugbrain.corpus.schema import (
    CorpusDeclaration,
    CorpusDomain,
    CorpusLanguage,
)
from rugbrain.qdrant_store.lane_table import CANONICAL_LANE_DIM

__all__ = [
    "CANONICAL_LANE_DIM",
    "CorpusDeclaration",
    "CorpusDomain",
    "CorpusLanguage",
    "client_for",
    "decl",
]


def decl(**over: object) -> CorpusDeclaration:
    """A complete declaration, overridable field by field."""
    base: dict[str, object] = {
        "name": "unit-test-corpus",
        "id": "unit-test-corpus",
        "language": CorpusLanguage.RUST,
        "domain": CorpusDomain.CODE,
        "collection": "codebase_reference_v2",
        "dim": CANONICAL_LANE_DIM,
        "source_url": "https://example.invalid/repo",
        "licence": "permissive",
        "refresh_cadence": "weekly",
        "last_verified": "2026-09-28",
    }
    base.update(over)
    return CorpusDeclaration(**base)  # type: ignore[arg-type]


def client_for(collections: dict[str, int | None]) -> httpx.AsyncClient:
    """A Qdrant stand-in. GET only: no PUT/DELETE route exists to reach.

    A collection mapped to ``None`` answers with an empty params block, which is
    what an unparseable width looks like — the measurement-gap path.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/collections":
            return httpx.Response(
                200,
                json={"result": {"collections": [{"name": n} for n in collections]}},
            )
        name = path.removeprefix("/collections/")
        if name in collections:
            dim = collections[name]
            if dim is None:
                return httpx.Response(200, json={"result": {"config": {"params": {}}}})
            return httpx.Response(
                200,
                json={"result": {"config": {"params": {"vectors": {"size": dim}}}}},
            )
        return httpx.Response(404, json={"result": None})

    return httpx.AsyncClient(
        base_url="http://qdrant.invalid:6334",
        transport=httpx.MockTransport(handler),
    )
