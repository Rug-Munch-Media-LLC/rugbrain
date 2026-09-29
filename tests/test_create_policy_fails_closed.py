"""MUTATION TESTS: prove the create-time width guard actually REFUSES.

WHY THIS FILE EXISTS
    A guard that has never been seen refusing is not a guard. Every test here
    drives the real ``_ensure_collection`` / ``insert`` code against a STUBBED
    HTTP layer (respx) and asserts the write path FAILS. Nothing touches live
    Qdrant (:6334 is production and is never created against, deleted from, or
    altered by this suite).

THE DEFECT UNDER TEST
    ``_ensure_collection`` defaulted ``create_if_missing=True``. For a genuinely
    unknown collection name the dimension guard therefore did not refuse — it
    CREATED a fresh collection at the caller's width and the write SUCCEEDED.
    A typo in a collection name forked a fresh empty corpus that then diverged
    forever, and nothing reported it. The live corpus still carries the scar: a
    collection literally named ``does_not_exist`` at the retired width.

THE TWO SHAPES
    A create/exists check must understand BOTH Qdrant vector-config shapes —
    the bare ``{"size": N}`` unnamed vector AND a named-vector mapping like
    ``{"dense": {"size": N}}`` (present whenever sparse/hybrid vectors are
    configured). An earlier attempt read only the bare shape and reported
    healthy populated hybrid collections as having NO vector config. These
    tests pin both shapes against the same parser.
"""

from __future__ import annotations

import pytest

from rugbrain.qdrant_store.collection_lifecycle import (
    CREATE_IF_MISSING_DEFAULT,
    _ensure_collection,
    _existing_collections,
)
from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions
from rugbrain.qdrant_store.create_policy import (
    CANONICAL_CREATE_DIM,
    RetiredWidthCreationRefused,
    assert_creatable_width,
)


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self) -> dict:
        return self._payload


class _StubClient:
    """Minimal Qdrant client double recording every MUTATING call.

    ``created`` is the assertion surface: if the guard fails open, the stub
    records a create and the test fails. A stub that silently accepted creates
    would be the same no-op as a green tick.
    """

    def __init__(self, *, exists: bool, dims: set[int] | None = None, shape: str = "bare") -> None:
        self.exists = exists
        self.dims = dims or {CANONICAL_CREATE_DIM}
        self.shape = shape
        self.created: list[tuple[str, int]] = []

    async def get(self, path: str) -> _Response:
        if not self.exists:
            return _Response(404)
        vectors: dict = {"size": next(iter(self.dims))}
        if self.shape == "named":
            vectors = {"dense": {"size": next(iter(self.dims)), "distance": "Cosine"}}
        return _Response(200, {"result": {"config": {"params": {"vectors": vectors}}}})

    async def put(self, path: str, json: dict | None = None) -> _Response:
        name = path.rsplit("/", 1)[-1]
        self.created.append((name, int((json or {}).get("vectors", {}).get("size", -1))))
        return _Response(201, {"result": True})


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _existing_collections.clear()


# --------------------------------------------------------------------------
# 1. The default is fail-closed, not create.
# --------------------------------------------------------------------------


def test_create_if_missing_default_refuses() -> None:
    """The DEFAULT must be refusal. A ``True`` default is the whole bug."""
    import inspect

    default = inspect.signature(_ensure_collection).parameters["create_if_missing"].default
    assert default == CREATE_IF_MISSING_DEFAULT
    assert default is not True, "create_if_missing must NOT default to True — that is the defect"
    assert default == "refuse"


@pytest.mark.asyncio
async def test_unknown_collection_refused_by_default() -> None:
    """An unknown name, default args: REFUSED, and nothing is created."""
    client = _StubClient(exists=False)
    ok = await _ensure_collection("does_not_exist", 2560, client)

    assert ok is False, "unknown collection must fail closed"
    assert client.created == [], f"fail-closed must not create; created={client.created}"


@pytest.mark.asyncio
async def test_unknown_collection_refused_even_at_canonical_width() -> None:
    """Fail-closed applies to the CANONICAL width too — this is about the NAME.

    Creation is a deliberate act regardless of width; otherwise the old
    ``create_if_missing=True`` default is merely narrowed, not fixed.
    """
    client = _StubClient(exists=False)
    ok = await _ensure_collection("typo_name", CANONICAL_CREATE_DIM, client)

    assert ok is False
    assert client.created == []


@pytest.mark.asyncio
async def test_explicit_false_refuses() -> None:
    client = _StubClient(exists=False)
    assert await _ensure_collection("x", 4096, client, create_if_missing=False) is False
    assert client.created == []


# --------------------------------------------------------------------------
# 2. Create-time width check: the retired generation is UNCREATABLE.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad_width", [2560, 1024, 2048, 1536])
def test_assert_creatable_width_refuses_non_canonical(bad_width: int) -> None:
    """Refuses 2560 (the retired lane) AND 1024 (the older pinned lane)."""
    with pytest.raises(RetiredWidthCreationRefused) as exc:
        assert_creatable_width("some_collection", bad_width)
    # Error must NAME both the width asked for and the canonical width.
    msg = str(exc.value)
    assert f"requested_dim={bad_width}" in msg
    assert f"canonical_dim={CANONICAL_CREATE_DIM}" in msg


def test_assert_creatable_width_allows_canonical() -> None:
    assert assert_creatable_width("some_collection", CANONICAL_CREATE_DIM) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_width", [2560, 1024])
async def test_create_at_retired_width_raises_even_when_explicitly_opted_in(
    bad_width: int,
) -> None:
    """Even a caller that NAMES ``create_if_missing=True`` cannot fork a bad width.

    This is the case the original bug lived in: the write path is a legitimate
    creator, so an explicit opt-in is the normal state. If the width check were
    only enforced on the default path, this would still create a 2560 fork.
    """
    client = _StubClient(exists=False)
    with pytest.raises(RetiredWidthCreationRefused):
        await _ensure_collection("typo", bad_width, client, create_if_missing=True)
    assert client.created == [], "a refused width must never reach the create call"


@pytest.mark.asyncio
async def test_canonical_create_succeeds_when_named() -> None:
    """The guard does NOT block a legitimate canonical create."""
    client = _StubClient(exists=False)
    ok = await _ensure_collection(
        "new_canonical", CANONICAL_CREATE_DIM, client, create_if_missing=True
    )

    assert ok is True
    assert client.created == [("new_canonical", CANONICAL_CREATE_DIM)]


# --------------------------------------------------------------------------
# 3. The write path: insert() must not silently fork.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_refuses_retired_width_unknown_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """insert() at 2560 into an unknown name: REFUSED, and no create is issued.

    This is the end-to-end proof of the reported mechanism. Against the old
    default this test would have seen a 201 create AND a successful write.
    """
    import respx

    from rugbrain.qdrant_store import QdrantVectorStore

    monkeypatch.setattr("rugbrain.qdrant_store._RMI_RAG_QDRANT_WRITE_MODE", "writable")
    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as mock:
        mock.get("/collections/does_not_exist").respond(404)
        create_route = mock.put("/collections/does_not_exist").respond(201, json={"result": True})
        put_points = mock.put("/collections/does_not_exist/points").respond(
            200, json={"result": {"status": "ok"}}
        )
        ok = await store.insert(doc_id="d", collection="does_not_exist", embedding=[0.1] * 2560)

    assert ok is False, "insert at the retired width must be refused, not written"
    assert not create_route.called, "a refused insert must not create a collection"
    assert not put_points.called, "a refused insert must not write points"


@pytest.mark.asyncio
async def test_insert_refuses_1024_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other retired width (1024) is refused on the same path."""
    import respx

    from rugbrain.qdrant_store import QdrantVectorStore

    monkeypatch.setattr("rugbrain.qdrant_store._RMI_RAG_QDRANT_WRITE_MODE", "writable")
    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as mock:
        mock.get("/collections/legacy_1024").respond(404)
        create_route = mock.put("/collections/legacy_1024").respond(201, json={"result": True})
        ok = await store.insert(doc_id="d", collection="legacy_1024", embedding=[0.1] * 1024)

    assert ok is False
    assert not create_route.called


# --------------------------------------------------------------------------
# 4. Reads must not create either.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_unknown_collection_refused_by_default() -> None:
    """A read against an unknown name returns empty and creates nothing."""
    import respx

    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as mock:
        mock.get("/collections/no_such_thing").respond(404)
        create_route = mock.put("/collections/no_such_thing").respond(201, json={"result": True})
        results = await store.search(query_embedding=[0.1] * 4096, collection="no_such_thing")

    assert results == []
    assert not create_route.called, "a read must never create a collection"


@pytest.mark.asyncio
async def test_hybrid_search_unknown_collection_refused_by_default() -> None:
    import respx

    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as mock:
        mock.get("/collections/no_such_hybrid").respond(404)
        create_route = mock.put("/collections/no_such_hybrid").respond(201, json={"result": True})
        results = await store.hybrid_search(
            query_text="q", query_embedding=[0.1] * 4096, collection="no_such_hybrid"
        )

    assert results == []
    assert not create_route.called


# --------------------------------------------------------------------------
# 5. BOTH Qdrant vector-config shapes are understood.
# --------------------------------------------------------------------------


def test_shape_parsing_bare_unnamed_vector() -> None:
    """Bare shape: ``{"vectors": {"size": N}}``."""
    assert dense_vector_dimensions({"vectors": {"size": 4096, "distance": "Cosine"}}) == {4096}
    assert dense_vector_dimensions({"vectors": {"size": 2560, "distance": "Cosine"}}) == {2560}


def test_shape_parsing_named_vector_mapping() -> None:
    """Named shape: ``{"vectors": {"dense": {"size": N}}}`` (hybrid/sparse)."""
    params = {
        "vectors": {
            "dense": {"size": 4096, "distance": "Cosine"},
            "bm25": {"sparse": True, "modifier": "idf"},
        }
    }
    assert dense_vector_dimensions(params) == {4096}


def test_shape_parsing_named_vector_still_detects_retired_width() -> None:
    """A named-vector collection at 2560 must be DETECTED, not reported empty.

    This is the failure mode of the earlier attempt: reading only the bare
    shape made a populated hybrid collection look like it had no vector config
    at all, so the guard passed on the very collections it should refuse.
    """
    params = {"vectors": {"dense": {"size": 2560, "distance": "Cosine"}}}
    assert dense_vector_dimensions(params) == {2560}, "named 2560 must be seen as 2560"


@pytest.mark.asyncio
async def test_existing_named_vector_2560_blocks_a_4096_write() -> None:
    """Existing hybrid collection at 2560 refuses a 4096 vector (both shapes)."""
    client = _StubClient(exists=True, dims={2560}, shape="named")
    ok = await _ensure_collection("hybrid_coll", 4096, client, create_if_missing=True)
    assert ok is False
    assert client.created == []


@pytest.mark.asyncio
async def test_existing_bare_vector_2560_blocks_a_4096_write() -> None:
    client = _StubClient(exists=True, dims={2560}, shape="bare")
    ok = await _ensure_collection("bare_coll", 4096, client, create_if_missing=True)
    assert ok is False
    assert client.created == []


@pytest.mark.asyncio
async def test_existing_2560_collection_still_accepts_a_2560_vector() -> None:
    """The preserved lane keeps working against its EXISTING collection.

    2560 is not universally illegal — it is a real physical width for pinned
    corpora (``rug_events``, ``coding_knowledge``, …). The create-time refusal
    governs unknown names; an existing, correctly-populated preserved lane must
    not be collateral damage of this guard.
    """
    client = _StubClient(exists=True, dims={2560}, shape="bare")
    ok = await _ensure_collection("rug_events", 2560, client, create_if_missing=True)
    assert ok is True
    assert client.created == []


# --------------------------------------------------------------------------
# 6. Mutation-proof: the refusal must survive weakening the guard.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_is_load_bearing_neutered_check_allows_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION CONTROL: neuter the width check; the refusal must disappear.

    A guard test that passes whether or not the guard exists proves nothing.
    This asserts the check is load-bearing: with ``assert_creatable_width``
    replaced by a no-op, creating at 2560 succeeds — so the refusals asserted
    above are produced by the check, not by the 404 alone.
    """
    import rugbrain.qdrant_store.collection_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "assert_creatable_width", lambda *a, **k: None)
    _existing_collections.clear()

    client = _StubClient(exists=False)
    ok = await lifecycle._ensure_collection("mutant", 2560, client, create_if_missing=True)

    assert ok is True, "neutering the check should let the create through"
    assert client.created == [("mutant", 2560)]


@pytest.mark.asyncio
async def test_pre_fix_behaviour_forked_and_is_now_impossible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION CONTROL: reproduce the PRE-FIX fork by neutering both guards.

    Two independent defects are in play and each is covered above on its own:
    the ``True`` default (creation happened for unknown names) and the missing
    width check (the creation was at the retired width). Here BOTH are
    removed, restoring exactly the old code path, and the reported symptom
    appears: ``does_not_exist`` — the literal name sitting in the live corpus —
    is created at 2560 and the call SUCCEEDS.

    That is the defect reproduced, and it is unreachable again.
    """
    import rugbrain.qdrant_store.collection_lifecycle as lifecycle
    from rugbrain.qdrant_store.create_policy import assert_creatable_width as real_check

    monkeypatch.setattr(lifecycle, "assert_creatable_width", lambda *a, **k: None)
    _existing_collections.clear()

    client = _StubClient(exists=False)
    ok = await lifecycle._ensure_collection("does_not_exist", 2560, client, create_if_missing=True)

    assert ok is True
    assert client.created == [("does_not_exist", 2560)]

    # And with the guards intact the identical call is refused.
    monkeypatch.setattr(lifecycle, "assert_creatable_width", real_check)
    _existing_collections.clear()
    guarded = _StubClient(exists=False)
    with pytest.raises(RetiredWidthCreationRefused):
        await _ensure_collection("does_not_exist", 2560, guarded, create_if_missing=True)
    assert guarded.created == []
