"""Embedder consolidation doctrine (2026-08-29, supersedes 2026-08-26 strain tests).

* ONE canonical embedder: qwen3-embedding-4b @ 2560 — DeepInfra primary,
  local :8004 fallback (SAME model ⇒ same vector space, fallback is
  index-compatible).
* Auto chain NEVER touches cross-model lanes (gateway :8010, OpenRouter,
  NVIDIA NIM) — those are explicit RAG_EMBED_BACKEND overrides only.
* T04 fail-closed contract survives: when every lane is down, embedding
  raises (no hash fallback, no zero vectors, no silent empties).
* The 0.6b speed-embed tier is LEGACY READS ONLY.
"""

from __future__ import annotations

import pytest

from rugbrain import embeddings as emb


@pytest.fixture(autouse=True)
def _no_live_lanes(monkeypatch):
    """Keep every test hermetic: no live DeepInfra / :8004 probes."""
    monkeypatch.setattr(emb, "_int_reachable", lambda: True)

    async def _reachable_async():
        return True

    monkeypatch.setattr(emb, "_int_reachable_async", _reachable_async, raising=False)
    monkeypatch.setenv("RAG_QUERY_CLOUD_FALLBACK", "1")
    yield


@pytest.mark.asyncio
async def test_query_served_by_deepinfra_primary(monkeypatch):
    """Queries resolve via the canonical DeepInfra lane (4096, same space)."""
    called: list[str] = []

    async def _di(text, dim=emb.EMBEDDING_DIM, query=False, timeout=30.0):  # noqa: ASYNC109 - mirrors prod signature
        called.append("deepinfra")
        assert query is True, "query lane must apply the Instruct template"
        return [0.1] * dim

    monkeypatch.setattr(emb, "_deepinfra_embed", _di)
    vec = await emb.get_query_embedding("flash loan attack")
    assert len(vec) == emb.EMBEDDING_DIM == 4096
    assert called == ["deepinfra"]


@pytest.mark.asyncio
async def test_ingest_fails_closed_when_all_lanes_down(monkeypatch):
    """T04: no lane available → raise (never hash/zero/silent empty)."""

    async def _none(*a, **kw):
        return None

    monkeypatch.setattr(emb, "_deepinfra_embed", _none)
    monkeypatch.setattr(emb, "_int_embed", _none)

    with pytest.raises(emb.RAGUnavailableError):
        await emb.get_embedding("document body for indexing")


@pytest.mark.asyncio
async def test_query_timeout_is_snappy_by_default(monkeypatch):
    """Query hard timeout must be short (strain failover), docs long."""
    seen: dict[str, float] = {}

    async def _di(text, dim=emb.EMBEDDING_DIM, query=False, timeout=30.0):  # noqa: ASYNC109 - mirrors prod signatures
        seen["di_timeout"] = timeout
        return None

    async def _int_embed(text, dim=2560, query=False, timeout=300.0):  # noqa: ASYNC109 - mirrors prod signatures
        seen["int_timeout"] = timeout
        return None

    monkeypatch.setattr(emb, "_deepinfra_embed", _di)
    monkeypatch.setattr(emb, "_int_embed", _int_embed)

    with pytest.raises(emb.RAGUnavailableError):
        await emb.get_query_embedding("q")
    assert seen["di_timeout"] <= 45.0  # query lane never inherits the doc timeout
    assert seen["int_timeout"] <= 45.0


@pytest.mark.asyncio
async def test_auto_chain_never_touches_cross_model_lanes(monkeypatch):
    """2026-08-29: auto = DeepInfra → local :8004 only. Gateway/OR/NIM are
    override-only; no env flag may pull them back into the auto chain."""

    async def _must_not_call(*a, **kw):
        raise AssertionError("cross-model lane must not be called in auto mode")

    async def _none(*a, **kw):
        return None

    monkeypatch.setenv("RAG_ALLOW_CROSS_MODEL_EMBED", "1")
    monkeypatch.setenv("RAG_QUERY_CLOUD_FALLBACK", "1")
    monkeypatch.setattr(emb, "_deepinfra_embed", _none)
    monkeypatch.setattr(emb, "_int_embed", _none)
    monkeypatch.setattr(emb, "_gateway_embed", _must_not_call)
    monkeypatch.setattr(emb, "_openrouter_embed", _must_not_call)
    monkeypatch.setattr(emb, "_nim_embed", _must_not_call)

    with pytest.raises(emb.RAGUnavailableError):
        await emb.get_query_embedding("q")
    with pytest.raises(emb.RAGUnavailableError):
        await emb.get_embedding("doc")


@pytest.mark.asyncio
async def test_openrouter_only_via_explicit_override(monkeypatch):
    """OR/NIM remain reachable ONLY via RAG_EMBED_BACKEND=openrouter."""
    calls: list[str] = []

    async def _or(text, dim=2560, allow_cross_model=None):
        calls.append("openrouter")
        return None

    async def _nim(text, dim=2560, allow_cross_model=None):
        calls.append("nim")
        return None

    monkeypatch.setattr(emb, "_BACKEND", "openrouter")
    monkeypatch.setattr(emb, "_openrouter_embed", _or)
    monkeypatch.setattr(emb, "_nim_embed", _nim)

    with pytest.raises(emb.RAGUnavailableError):
        await emb.get_embedding("doc")
    assert calls == ["openrouter", "nim"]  # explicit override ladder intact
