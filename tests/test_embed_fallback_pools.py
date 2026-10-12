"""Tests for dim_policy registry + embed fallback pools (2026-08-25 quality push).

Covers the multi-dim safety contract: registry truth, MRL truncate+renormalize,
undersize rejection, OR key-pool rotation, and NIM tier config parsing.
All network mocks use fresh clients per call (fleet lesson: NEVER share one
AsyncClient instance via return_value=).
"""

from __future__ import annotations

import math

import httpx
import pytest

from rugbrain import embeddings as emb
from rugbrain.dim_policy import (
    default_dim_for_model,
    native_dims,
    normalize_dims,
)

# ── Registry ─────────────────────────────────────────────────────────────


def test_registry_native_dims_match_verified_reality():
    # Values verified live 2026-08-25 against :8004/DeepInfra; the 0.6B
    # speed tier was retired 2026-09-14.
    # 2026-10-12 (doctrine update): canonical lane is 8B @ 4096; qwen3-embedding-8b
    # native dim is 4096. The 4B/2560 generation is retired (returns None).
    assert native_dims("qwen3-embedding-8b") == 4096
    assert native_dims("nvidia/nemotron-3-embed-1b") == 2048
    assert native_dims("nvidia/llama-nemotron-embed-vl-1b-v2") == 2048


def test_registry_unknown_model_returns_none_and_fallback():
    assert native_dims("totally-unknown-model") is None
    assert default_dim_for_model("totally-unknown-model", fallback=999) == 999
    # 2026-10-12: default_dim_for_model for canonical model returns 4096
    assert default_dim_for_model("qwen3-embedding-8b") == 4096


def test_registry_lookup_is_whitespace_case_tolerant():
    assert native_dims("  Qwen3-Embedding-8B ") == 4096


# ── normalize_dims contract (2026-09-30 doctrine: REFUSES to truncate) ─────


def test_normalize_exact_size_is_l2_normalized_passthrough():
    v = [3.0, 4.0]
    out = normalize_dims(v, 2)
    assert len(out) == 2
    assert math.isclose(sum(x * x for x in out), 1.0)


def test_normalize_oversize_raises_refusing_to_truncate():
    """2026-09-30 doctrine: normalize_dims REFUSES to truncate oversize vectors.
    An oversize vector raises ValueError mentioning 'refusing to truncate'."""
    v = [1.0] * 8
    with pytest.raises(ValueError, match="refusing to truncate"):
        normalize_dims(v, 4)


def test_normalize_oversize_raises_with_dim_info():
    """Error message names both the source and target dims."""
    v = [2.0, 0.0, 2.0, 0.0]
    with pytest.raises(ValueError, match="4-dim vector to 2"):
        normalize_dims(v, 2)


def test_normalize_zero_vector_exact_size_returns_zeros():
    """Exact-size zero vector is L2-normalized to zeros (no crash)."""
    out = normalize_dims([0.0, 0.0], 2)
    assert out == [0.0, 0.0]


def test_normalize_undersize_rejects_loud():
    with pytest.raises(ValueError, match="dim"):
        normalize_dims([1.0, 2.0], 4)


# ── Guard: no 4B/2560 lane reachable ─────────────────────────────────────


def test_retired_4b_model_returns_none():
    """The 4B/2560 generation is retired — native_dims returns None."""
    assert native_dims("qwen3-embedding-4b") is None


def test_retired_4b_model_raises_on_use():
    """Using the retired 4B model via assert_writable_model raises."""
    from rugbrain.dim_policy import assert_writable_model
    with pytest.raises(ValueError, match=r"retired.*use.*qwen3-embedding-8b"):
        assert_writable_model("qwen3-embedding-4b")


# ── OpenRouter key pool ──────────────────────────────────────────────────


def test_key_pool_prefers_multi_key_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEYS", "key-a, key-b ,")
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy")
    pool = emb._or_key_pool()
    assert pool == ["key-a", "key-b"]


def test_key_pool_falls_back_to_single_legacy(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy-only")
    assert emb._or_key_pool() == ["legacy-only"]


def test_key_pool_empty_when_nothing_set(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert emb._or_key_pool() == []


def test_openrouter_embed_rotates_accounts(monkeypatch):
    """Two keys → consecutive calls alternate accounts (header evidence)."""
    monkeypatch.setenv("OPENROUTER_API_KEYS", "key-aaaa,key-bbbb")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"data": [{"embedding": [0.5] * 2048, "index": 0}]})

    import asyncio

    async def one_call():
        transport = httpx.MockTransport(handler)
        from rugbrain.embeddings_pkg import legacy_lanes

        orig = legacy_lanes.httpx.AsyncClient

        class C(orig):
            def __init__(self, *a, **k):
                k.pop("transport", None)
                super().__init__(*a, transport=transport, **k)

        monkeypatch.setattr(legacy_lanes.httpx, "AsyncClient", C)
        return await emb._openrouter_embed("x", dim=2048)

    outs = [asyncio.run(one_call()) for _ in range(4)]
    assert all(o is not None and len(o) == 2048 for o in outs)
    assert len(set(seen)) == 2, f"expected both keys used, saw {set(seen)}"
    # Strict alternation across the 4 calls
    tails = [s.split()[-1] for s in seen]
    assert tails[0] != tails[1] and tails[0] == tails[2] and tails[1] == tails[3]


def test_openrouter_embed_429_fails_over_to_second_key(monkeypatch):
    """First key rate-limited → second key serves; result still returned."""
    monkeypatch.setenv("OPENROUTER_API_KEYS", "key-limited,key-fresh")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Authorization"])
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json={"data": [{"embedding": [1.0] * 2048, "index": 0}]})

    import asyncio

    async def once():
        transport = httpx.MockTransport(handler)
        from rugbrain.embeddings_pkg import legacy_lanes

        orig = legacy_lanes.httpx.AsyncClient

        class C(orig):
            def __init__(self, *a, **k):
                k.pop("transport", None)
                super().__init__(*a, transport=transport, **k)

        monkeypatch.setattr(legacy_lanes.httpx, "AsyncClient", C)
        emb._OR_KEY_CURSOR = __import__("itertools").count()  # deterministic start
        return await emb._openrouter_embed("x", dim=2048)

    out = asyncio.run(once())
    assert out is not None and len(out) == 2048
    assert len(calls) == 2, "429 must rotate to next key"


# ── NIM tier config ──────────────────────────────────────────────────────


def test_nim_models_default_list():
    assert emb.NIM_MODELS == [
        "nvidia/nemotron-3-embed-1b",
        "nvidia/llama-nemotron-embed-vl-1b-v2",
    ]
    assert emb.NIM_URL.startswith("https://integrate.api.nvidia.com")


def test_nim_embed_skips_when_no_key(monkeypatch):
    monkeypatch.setattr(emb, "NIM_KEY", "")

    import asyncio

    assert asyncio.run(emb._nim_embed("x")) is None
