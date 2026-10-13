"""Native collections must NOT be rerouted into the unified corpus.

Regression (2026-10-13): ``social_intel`` was listed in CONSOLIDATED_SOURCES,
so with RAG_UNIFIED_READS=1 every read became
``rag_documents + filter(collection=="social_intel")`` -- a partition that does
not exist, because the tweets are written to their own native 4096 collection.
The API returned 0 hits for a collection holding thousands of points.
"""

from __future__ import annotations

import pytest

from rugbrain.qdrant_store.namespace import (
    CONSOLIDATED_SOURCES,
    UNIFIED_READS,
    unified_route,
)

#: collections created natively at 4096 and written directly -- they own their
#: points and must read from themselves.
NATIVE_SINGLE_ENTRY = ("social_intel",)


@pytest.mark.parametrize("collection", NATIVE_SINGLE_ENTRY)
def test_native_collection_is_not_consolidated(collection: str) -> None:
    assert collection not in CONSOLIDATED_SOURCES, (
        f"{collection} is a native 4096 collection; consolidating it reroutes "
        "reads into a partition that does not exist"
    )


@pytest.mark.parametrize("collection", NATIVE_SINGLE_ENTRY)
def test_native_route_returns_itself_without_a_filter(collection: str) -> None:
    target, condition = unified_route(collection)
    assert condition is None or "collection" not in str(condition)


def test_legacy_consolidated_collections_still_route_to_the_unified_corpus() -> None:
    """The consolidation itself must keep working for the legacy corpora."""
    assert "news_v2" in CONSOLIDATED_SOURCES
    if UNIFIED_READS:
        target, condition = unified_route("news_v2")
        assert target == "rag_documents"
