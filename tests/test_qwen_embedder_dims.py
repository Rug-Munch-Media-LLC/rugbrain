"""Qwen3Embedder dim-handling contracts (registry-aware, post 2026-08-25).

The embedder's native dim comes from ``dim_policy.MODEL_DIMS``
(qwen3-embedding-8b serves 4096, DeepInfra canonical). The embedder must:
* default DIMS to the registry value for the configured model
* accept exact-dim vectors
* MRL-TRUNCATE + RENORMALIZE larger vectors (never reject)
* REJECT undersized vectors (cannot fabricate missing dims — T04/TOP-100#25:
  silent pad/truncate poisoned retrieval spaces)
"""

from __future__ import annotations

import pytest

from rugbrain.dim_policy import default_dim_for_model
from rugbrain.qwen_embedders import Qwen3Embedder


def test_dims_default_matches_collections():
    """DIMS must track the dim registry for the configured model."""
    assert default_dim_for_model(Qwen3Embedder.MODEL) == Qwen3Embedder.DIMS
    # and the registry pins qwen3-embedding-8b at native 4096
    assert Qwen3Embedder.DIMS == 4096


def _client_returning(vec: list[float]) -> type:
    class Resp:
        status_code = 200

        def json(self):
            return {"data": [{"embedding": vec}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, url, json=None):
            return Resp()

    return Client


@pytest.mark.asyncio
async def test_exact_dim_accepted(monkeypatch):
    e = Qwen3Embedder()
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _client_returning([0.5] * e.DIMS)())
    out = await e.embed(["hello"])
    assert len(out[0]) == e.DIMS


@pytest.mark.asyncio
async def test_oversized_vector_rejected_not_truncated(monkeypatch):
    """2026-09-30 doctrine: downscaling is REFUSED, not MRL-truncated."""
    e = Qwen3Embedder()
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _client_returning([2.0] * (e.DIMS + 1536))())
    with pytest.raises((ValueError, RuntimeError), match="truncate|dim mismatch"):
        await e.embed(["hello"])


@pytest.mark.asyncio
async def test_undersized_vector_rejected(monkeypatch):
    """Undersized vectors must FAIL LOUD (T04): never fabricate missing dims."""
    e = Qwen3Embedder()
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _client_returning([0.5] * (e.DIMS - 1024))())
    with pytest.raises(RuntimeError, match="dim mismatch"):
        await e.embed(["hello"])
