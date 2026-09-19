"""NearDuplicateIndex contracts — in-process fast-embedder dedup.

* identical/near-identical text flagged at threshold
* distinct text passes and enters the window
* embedder failure → fail-open (False, 0.0), nothing added
* TTL + window eviction bound memory
* stats counters
"""

from __future__ import annotations

import pytest

from rugbrain.near_dup import NearDuplicateIndex


class StubEmbedder:
    """Deterministic embeddings: explicit map first, else hash-derived
    orthogonal-ish vector so distinct texts are NOT near-duplicates."""

    def __init__(self, vectors: dict[str, list[float]], default: list[float] | None = None):
        self.vectors = vectors
        self.default = default
        self.calls = 0

    async def embed_one(self, text):
        self.calls += 1
        if text in self.vectors:
            return self.vectors[text]
        if self.default is not None:
            return self.default
        # derive a stable pseudo-vector from the text — hashlib, NOT builtin
        # hash(): str hashing is PYTHONHASHSEED-randomized, which made two
        # "unique-*" texts collide in CI and fail the eviction assertion.
        import hashlib

        h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
        dim = 8
        vec = [0.0] * dim
        vec[h % dim] = 1.0
        vec[(h // dim) % dim] = 0.5
        return vec


@pytest.mark.asyncio
async def test_identical_text_flagged():
    idx = NearDuplicateIndex(StubEmbedder({}), threshold=0.9)
    await idx.check_and_add("k1", "original text")
    dup, sim = await idx.check_and_add("k2", "original text")
    assert dup is True
    assert sim == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_distinct_text_passes():
    idx = NearDuplicateIndex(
        StubEmbedder(
            {
                "cats": [1.0, 0.0, 0.0],
                "blockchain": [0.0, 1.0, 0.0],
            }
        )
    )
    dup1, _ = await idx.check_and_add("k1", "cats")
    dup2, _ = await idx.check_and_add("k2", "blockchain")
    assert dup1 is False and dup2 is False


@pytest.mark.asyncio
async def test_embedder_failure_fail_open():
    class Boom:
        async def embed_one(self, text):
            raise ConnectionError("8007 down")

    idx = NearDuplicateIndex(Boom())
    dup, sim = await idx.check_and_add("k", "text")
    assert (dup, sim) == (False, 0.0)
    assert len(idx._store) == 0  # nothing added on failure


@pytest.mark.asyncio
async def test_window_eviction_bounds_memory():
    idx = NearDuplicateIndex(StubEmbedder({}), window=3)
    for i in range(5):
        await idx.check_and_add(f"k{i}", f"unique-{i}")
    assert len(idx._store) == 3


@pytest.mark.asyncio
async def test_ttl_eviction():
    idx = NearDuplicateIndex(StubEmbedder({}), ttl_s=10)
    await idx.check_and_add("old", "unique-old")
    # age out the entry
    key, (vec, ts) = next(iter(idx._store.items()))
    idx._store[key] = (vec, ts - 100)
    await idx.check_and_add("fresh", "unique-fresh")
    assert "old" not in idx._store


@pytest.mark.asyncio
async def test_stats_counters():
    idx = NearDuplicateIndex(StubEmbedder({}))
    await idx.check_and_add("a", "first unique")
    await idx.check_and_add("b", "first unique")  # dup
    st = idx.stats()
    assert st["duplicates_seen"] == 1
    assert st["window_used"] == 1
