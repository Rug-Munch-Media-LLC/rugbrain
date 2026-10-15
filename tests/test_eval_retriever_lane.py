"""Regression: the eval retriever must resolve the lane AND match the embedder.

The defect this locks down is the third instance of one bug (be0da01 search
path, 24915db mission-eval retriever): a component that names a collection to
Qdrant without going through ``lane_resolver.resolve_collection``.

Two failure modes are covered, and the second is the one a naive fix creates:

1. Not resolving — the golden row's raw name reaches Qdrant, and a migrated
   base is queried with the canonical-width vector.
2. Resolving without matching the embedder — BOTH generations are populated, so
   resolving a base to its 4096 twin while still embedding at 2560 turns every
   working row into HTTP 400. Resolution alone is a regression.

No Qdrant and no network: the retriever's two collaborators (live width reader,
embedder) are injected, so this asserts the DECISION, not a live server.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from rugbrain.eval import qdrant_retriever as qr

#: What live :6334 returned on 2026-09-29 for these names. The point counts are
#: irrelevant here; the widths are the fact under test.
LIVE_WIDTHS = {
    "defi_exploits": 2560,
    "defi_exploits_8b": 4096,
    "threat_intel_v2": 2560,
    "threat_intel_v2_8b": 4096,
    "coding_knowledge_8b": 4096,
    "solana_patterns_8b": 4096,
    "security_patterns": 4096,  # base is 4096 but near-empty (278 pts)
    "security_patterns_8b": 4096,  # the twin holds the corpus (7,063 pts)
    "public_crypto_patterns": 2560,  # still pinned (no twin)
}


class _FakeQdrant:
    """Serves the two GETs the retriever makes and records the search target."""

    def __init__(self) -> None:
        self.searched: list[tuple[str, int]] = []

    def width_for(self, collection: str) -> int | None:
        return LIVE_WIDTHS.get(collection)


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    qr._WIDTH_CACHE.clear()
    qr._NAMED_CACHE.clear()
    yield
    qr._WIDTH_CACHE.clear()
    qr._NAMED_CACHE.clear()


def _embed_for_width_stub(canonical: Any, preserved: Any) -> Any:
    """Preserve the real width->lane MAPPING, replace only the backend calls.

    The mapping is the behaviour under test, so it is reused rather than
    rewritten: only the network-backed embedders are swapped.
    """

    async def _stub(width: int, query: str) -> list[float]:
        if width == 4096:
            return await canonical(query)
        if width == 2560:
            return await preserved(query)
        raise qr.LaneMismatch(f"no embedding lane for width {width}; refusing to guess")

    return _stub


def _patch_io(monkeypatch: pytest.MonkeyPatch, fake: _FakeQdrant) -> None:
    """Replace the HTTP layer and both embedder lanes; keep the real resolver.

    Both embedding lanes are stubbed with distinct widths so the test asserts
    that lane SELECTION happens and is correct, without needing a live
    DeepInfra/llama.cpp backend.
    """

    async def _canonical(text: str, query: bool = True) -> list[float]:
        return [0.0] * 4096

    async def _preserved(text: str) -> list[float]:
        return [0.0] * 2560

    monkeypatch.setattr(qr, "_live_width", lambda base, coll: fake.width_for(coll))
    monkeypatch.setattr(qr, "_named_vectors", lambda base, coll: False)
    monkeypatch.setattr(qr, "_embed_for_width", _embed_for_width_stub(_canonical, _preserved))
    monkeypatch.setattr(qr, "_WIDTH_CACHE", {})

    def _fake_urlopen(req: Any, timeout: int = 30) -> Any:
        coll = req.full_url.split("/collections/")[1].split("/")[0]
        body = json.loads(req.data.decode())
        width = len(body["vector"])
        fake.searched.append((coll, width))
        assert LIVE_WIDTHS[coll] == width, (
            f"retriever embedded {width} against {coll} which stores "
            f"{LIVE_WIDTHS[coll]} — this is the silent-wrong-lane bug"
        )

        class _Resp:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *a: Any) -> bool:
                return False

            def read(self) -> bytes:
                return b'{"result": [{"id": "1", "score": 0.9, "payload": {}}]}'

        return _Resp()

    monkeypatch.setattr(qr.urllib.request, "urlopen", _fake_urlopen)


@pytest.mark.asyncio
async def test_base_name_resolves_to_twin_and_uses_canonical_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A migrated BASE must be resolved to its twin, then embedded at 4096."""
    fake = _FakeQdrant()
    _patch_io(monkeypatch, fake)
    retrieve = qr.direct_qdrant_retriever(qdrant_url="http://stub")

    await retrieve("oracle manipulation", "defi_exploits", 5)

    assert fake.searched == [("defi_exploits_8b", 4096)], fake.searched


@pytest.mark.asyncio
async def test_pinned_2560_collection_stays_on_the_preserved_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pinned 2560 collection must NOT be pushed onto the canonical lane.

    ``public_crypto_patterns`` remains pinned to the retired generation with no
    ``_8b`` twin, so a 4096 vector can never enter it. This is a DELIBERATE
    exception, not an oversight — the retriever must keep embedding 2560 for it.
    (``coding_knowledge`` / ``solana_patterns`` moved to ``_8b`` twins on
    2026-10-15 and are covered by the twin tests.)
    """
    fake = _FakeQdrant()
    _patch_io(monkeypatch, fake)
    retrieve = qr.direct_qdrant_retriever(qdrant_url="http://stub")

    await retrieve("reentrancy", "public_crypto_patterns", 5)

    assert fake.searched == [("public_crypto_patterns", 2560)], fake.searched


@pytest.mark.asyncio
async def test_already_resolved_twin_name_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row naming ``*_8b`` must not gain a second suffix."""
    fake = _FakeQdrant()
    _patch_io(monkeypatch, fake)
    retrieve = qr.direct_qdrant_retriever(qdrant_url="http://stub")

    await retrieve("oracle manipulation", "defi_exploits_8b", 5)

    assert fake.searched == [("defi_exploits_8b", 4096)], fake.searched


@pytest.mark.asyncio
async def test_security_patterns_row_searches_the_twin_not_the_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The routing gap: a ``security_patterns`` row must search the twin.

    Both names are 4096, so a width check alone cannot catch this — the base is
    a near-empty 278-point collection while the twin holds the corpus. Before
    the fix the row searched ``security_patterns`` and the golden ZK chunk
    (present only in the twin) could never be returned.
    """
    fake = _FakeQdrant()
    _patch_io(monkeypatch, fake)
    retrieve = qr.direct_qdrant_retriever(qdrant_url="http://stub")

    await retrieve("zk circuit underconstrained proof vulnerability", "security_patterns", 5)

    assert fake.searched == [("security_patterns_8b", 4096)], fake.searched


@pytest.mark.asyncio
async def test_unknown_width_raises_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collection whose width matches no lane must raise, never truncate."""
    monkeypatch.setattr(qr, "_named_vectors", lambda base, coll: False)
    monkeypatch.setattr(qr, "_live_width", lambda base, coll: 1024)
    monkeypatch.setattr(qr, "_WIDTH_CACHE", {})
    retrieve = qr.direct_qdrant_retriever(qdrant_url="http://stub")

    with pytest.raises(qr.LaneMismatch):
        await retrieve("anything", "wallet_clusters", 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("width,expected", [(4096, 4096), (2560, 2560)])
async def test_real_lane_mapping_returns_that_width(
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    expected: int,
) -> None:
    """The REAL ``_embed_for_width`` must return a vector of the width asked.

    This is the mutation guard for the mapping itself. The other tests stub the
    embedder, so without this one, swapping the 4096 and 2560 lanes — which
    silently breaks every migrated collection while keeping the 2560 pinned
    ones working — would pass the whole file.
    """

    async def _canonical(text: str, query: bool = True) -> list[float]:
        return [0.0] * 4096

    async def _preserved(text: str) -> list[float]:
        return [0.0] * 2560

    monkeypatch.setattr("rugbrain.embeddings.get_embedding", _canonical)
    monkeypatch.setattr("rugbrain.query_embedder.embed_query", _preserved)

    vector = await qr._embed_for_width(width, "probe")
    assert len(vector) == expected


@pytest.mark.asyncio
async def test_no_collection_returns_empty_without_touching_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A golden row with no collection must short-circuit, not query."""
    fake = _FakeQdrant()
    _patch_io(monkeypatch, fake)
    retrieve = qr.direct_qdrant_retriever(qdrant_url="http://stub")

    assert await retrieve("query", None, 5) == []
    assert fake.searched == []


def test_live_width_table_matches_the_declared_lane() -> None:
    """Guard the assumption the fake encodes: the resolver maps base -> twin.

    If ``lane_table`` changes, this fails loudly rather than letting the fakes
    describe a world that no longer exists.
    """
    from rugbrain.qdrant_store.lane_resolver import resolve_collection

    for base in ("defi_exploits", "threat_intel_v2"):
        assert resolve_collection(base) == f"{base}_8b"
    for base in ("coding_knowledge", "solana_patterns", "security_patterns"):
        assert resolve_collection(base) == f"{base}_8b"
