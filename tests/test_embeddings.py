from rugbrain.embeddings import EMBED_MODEL_NAME, EMBED_VERSION_STAMP, EMBEDDING_DIM


def test_embedding_dim() -> None:
    assert EMBEDDING_DIM == 2560


def test_model_name() -> None:
    assert EMBED_MODEL_NAME == "qwen3-embedding-4b"


def test_version_stamp() -> None:
    assert "qwen3-embedding-4b" in EMBED_VERSION_STAMP
    assert "2560" in EMBED_VERSION_STAMP
    assert ":" in EMBED_VERSION_STAMP


def test_embeddings_importable() -> None:
    from rugbrain.embeddings import current_backend, get_embedding, get_embeddings

    assert callable(get_embedding)
    assert callable(get_embeddings)
    assert callable(current_backend)


# --- TOP-100 #23/#25/#78: fail-loud regression tests (2026-08-23) -----------
# Contract: no silent hash vectors, no zero vectors, dim mismatches raise.


def test_resize_rejects_dim_mismatch() -> None:
    import pytest

    from rugbrain.embeddings import _resize

    with pytest.raises(ValueError, match="dim mismatch"):
        _resize([0.1] * 1024, 2560)


def test_resize_accepts_exact_dim() -> None:
    from rugbrain.embeddings import _resize

    out = _resize([3.0, 4.0], 2)
    assert len(out) == 2
    assert abs(sum(x * x for x in out) - 1.0) < 1e-5  # L2-normalized


def test_hash_embed_removed() -> None:
    """The silent hash fallback must not exist anywhere on CryptoEmbedder."""
    from rugbrain.crypto_embeddings import CryptoEmbedder

    assert not hasattr(CryptoEmbedder, "_hash_embed"), (
        "hash fallback resurrected — TOP-100 #23 violation"
    )


def test_semantic_embed_raises_when_all_providers_fail(monkeypatch) -> None:
    import pytest

    from rugbrain import crypto_embeddings as ce

    emb = ce.CryptoEmbedder.__new__(ce.CryptoEmbedder)
    emb._qwen = None
    emb._local = None
    emb._openrouter = None
    emb._huggingface = None
    with pytest.raises(RuntimeError, match="all embedding providers failed"):
        import asyncio

        asyncio.run(emb._semantic_embed(["x"], "semantic"))


def test_qwen_embedder_raises_instead_of_zero_vector(monkeypatch) -> None:
    """Engine failure must raise, never append a zero vector (TOP-100 #23)."""
    import pytest

    from rugbrain.qwen_embedders import Qwen3Embedder

    e = Qwen3Embedder(embed_url="http://127.0.0.1:1/v1")  # nothing listens here
    import asyncio

    with pytest.raises(RuntimeError):
        asyncio.run(e.embed(["hello"]))


def test_gateway_lane_refuses_cloud_vectors(monkeypatch) -> None:
    """Cloud-served gateway responses must NEVER enter local indexes."""
    import asyncio

    from rugbrain import embeddings as emb

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "tier": "deep",
                "served_by": "cloud",
                "cloud_model": "qwen/qwen3-embedding-8b",
                "model_dims": 4096,
                "embeddings": [[0.5] * 4096],
            }

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            return FakeResp()

    monkeypatch.setattr(emb.httpx, "AsyncClient", FakeClient)
    out = asyncio.run(emb._gateway_embed("test", dim=2560))
    assert out is None, "cloud vector must be refused for index use"


def test_gateway_lane_accepts_local_vectors(monkeypatch) -> None:
    import asyncio
    import math

    from rugbrain import embeddings as emb

    vec2560 = [1.0] + [0.0] * 2559

    class FakeResp:
        status_code = 200

        def json(self):
            return {"tier": "deep", "served_by": "local", "embeddings": [vec2560]}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            return FakeResp()

    monkeypatch.setattr(emb.httpx, "AsyncClient", FakeClient)
    out = asyncio.run(emb._gateway_embed("test", dim=2560))
    assert out is not None and len(out) == 2560
    assert math.isclose(sum(x * x for x in out), 1.0, rel_tol=1e-4)
