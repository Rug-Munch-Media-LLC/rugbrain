"""Qwen3Embedder dim-handling contracts (post 2026-08-24 fix).

The server serves MRL-truncated 1024-dim vectors. The embedder must:
* accept exact-dim vectors
* TRUNCATE + RENORMALIZE larger vectors (never reject — that broke every
  ingestion path when the server config moved to 1024)
* reject only undersized vectors (cannot fabricate missing dims)
"""

from __future__ import annotations

import pytest

from rugbrain.qwen_embedders import Qwen3Embedder


def test_dims_default_matches_collections():
    assert Qwen3Embedder.DIMS == 1024


@pytest.mark.asyncio
async def test_exact_dim_accepted(monkeypatch):
    e = Qwen3Embedder()

    class Resp:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [0.5] * 1024}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, json=None):
            return Resp()

    monkeypatch.setattr("httpx.AsyncClient", lambda **k: Client())
    out = await e.embed(["hello"])
    assert len(out[0]) == 1024


@pytest.mark.asyncio
async def test_oversized_vector_mrl_truncated_and_renormalized(monkeypatch):
    """The regression: 2560-dim output used to raise dim-mismatch and break
    every ingestion path. Now it truncates to DIMS and renormalizes."""
    e = Qwen3Embedder()

    class Resp:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [2.0] * 2560}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, json=None):
            return Resp()

    monkeypatch.setattr("httpx.AsyncClient", lambda **k: Client())
    out = await e.embed(["hello"])
    assert len(out[0]) == 1024
    norm = sum(x * x for x in out[0]) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_undersized_vector_rejected(monkeypatch):
    e = Qwen3Embedder()

    class Resp:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [0.5] * 384}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, json=None):
            return Resp()

    monkeypatch.setattr("httpx.AsyncClient", lambda **k: Client())
    with pytest.raises(RuntimeError, match="dim mismatch"):
        await e.embed(["hello"])


# ── Fast tier (0.6B) ──────────────────────────────────────────────────


def test_fast_embedder_targets_speed_server():
    from rugbrain.qwen_embedders import Qwen3FastEmbedder

    e = Qwen3FastEmbedder()
    assert ":8007" in e.url
    assert e.MODEL == "qwen3-embedding-0.6b"
    assert e.DIMS == 1024


def test_fast_embedder_is_deep_embedder_subtype():
    """Inherits MRL dim-handling from the deep embedder."""
    from rugbrain.qwen_embedders import Qwen3Embedder, Qwen3FastEmbedder

    assert issubclass(Qwen3FastEmbedder, Qwen3Embedder)


@pytest.mark.asyncio
async def test_fast_embedder_mrl_truncates(monkeypatch):
    from rugbrain.qwen_embedders import Qwen3FastEmbedder

    e = Qwen3FastEmbedder()

    class Resp:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": [1.0] * 2048}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, json=None):
            assert ":8007" in url
            return Resp()

    monkeypatch.setattr("httpx.AsyncClient", lambda **k: Client())
    out = await e.embed(["utility text"])
    assert len(out[0]) == 1024
