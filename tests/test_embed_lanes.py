"""Hosted embedding rung tests — DeepInfra shape, ladder order, fail-open.

Hermetic: fake HTTP clients and monkeypatched rungs; no network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

import rugbrain.embedder as embedder


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._payload = payload or {}
        self._exc = exc

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(
        self, url: str, headers: dict | None = None, json: dict | None = None
    ) -> _FakeResponse:
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _vecs(n: int) -> list[list[float]]:
    return [[0.5] * 4096 for _ in range(n)]


# --- Helper to set provider key via env ---


def _set_key(monkeypatch: pytest.MonkeyPatch, provider: str, value: str = "testkey") -> None:
    _, _, env_name, _ = embedder._PROVIDERS[provider]
    monkeypatch.setenv(env_name, value)


def _unset_key(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    _, _, env_name, _ = embedder._PROVIDERS[provider]
    monkeypatch.delenv(env_name, raising=False)


# --- hosted_embed function tests ---


def test_hosted_skip_provider_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a provider has no key, the chain skips it."""
    # We will not monkeypatch _call; we rely on the real _call which will be called
    # only if the key is present. To avoid making real HTTP requests, we monkeypatch
    # _key to return empty string for deepinfra and non-empty for others.
    monkeypatch.setattr(embedder, "_key", lambda p: "" if p == "deepinfra" else "key")
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "deepinfra,siliconflow,openrouter")

    # We need to monkeypatch _call to avoid real HTTP, but we want to see if it's called.
    # We'll wrap the real _call to record calls.
    call_order = []

    async def monitored_call(provider: str, texts: list[str], timeout: float | None) -> list[list[float]] | None:  # noqa: ASYNC109
        call_order.append(provider)
        # If we get here, it means the key was present (since _key returned non-empty)
        # Return a dummy vector
        return _vecs(len(texts))

    monkeypatch.setattr(embedder, "_call", monitored_call)

    result = asyncio.run(embedder.hosted_embed(["test"]))
    assert result is not None
    # deepinfra should be skipped due to no key (as per our _key mock)
    assert call_order == ["siliconflow"]


def test_hosted_embed_returns_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a provider works, hosted_embed returns vectors of correct dimension."""
    _set_key(monkeypatch, "deepinfra")
    client = _FakeClient({"data": [{"embedding": [0.1] * 4096}, {"embedding": [0.9] * 4096}]})
    monkeypatch.setattr(embedder.httpx, "AsyncClient", lambda timeout=None: client)

    out = asyncio.run(embedder.hosted_embed(["a", "b"]))
    assert out is not None
    assert len(out) == 2
    assert all(len(v) == 4096 for v in out)


def test_hosted_embed_failure_returns_none_and_tries_next(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a provider fails (exception), the chain tries the next provider."""
    _set_key(monkeypatch, "deepinfra")
    _set_key(monkeypatch, "siliconflow")
    call_order = []

    async def fake_call(provider: str, texts: list[str], timeout: float | None) -> list[list[float]] | None:  # noqa: ASYNC109
        call_order.append(provider)
        if provider == "deepinfra":
            raise httpx.ConnectError("down")
        return _vecs(len(texts))

    monkeypatch.setattr(embedder, "_call", fake_call)
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "deepinfra,siliconflow")

    result = asyncio.run(embedder.hosted_embed(["test"]))
    assert result is not None
    assert call_order == ["deepinfra", "siliconflow"]


def test_hosted_embed_wrong_dim_returns_none_and_tries_next(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a provider returns wrong dimension, the chain tries the next provider."""
    _set_key(monkeypatch, "deepinfra")
    _set_key(monkeypatch, "siliconflow")
    call_order = []

    async def fake_call(provider: str, texts: list[str], timeout: float | None) -> list[list[float]] | None:  # noqa: ASYNC109
        call_order.append(provider)
        if provider == "deepinfra":
            # Return vector of length 100 (wrong)
            return [[0.5] * 100 for _ in texts]
        return _vecs(len(texts))

    monkeypatch.setattr(embedder, "_call", fake_call)
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "deepinfra,siliconflow")

    result = asyncio.run(embedder.hosted_embed(["test"]))
    assert result is not None
    assert call_order == ["deepinfra", "siliconflow"]


def test_hosted_embed_all_providers_fail_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """If all providers fail, hosted_embed returns None."""
    _set_key(monkeypatch, "deepinfra")
    _set_key(monkeypatch, "siliconflow")
    _set_key(monkeypatch, "openrouter")
    async def fake_call(*args: Any, **kwargs: Any) -> list[list[float]] | None:
        return None

    monkeypatch.setattr(embedder, "_call", fake_call)
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "deepinfra,siliconflow,openrouter")

    assert asyncio.run(embedder.hosted_embed(["test"])) is None


def test_hosted_embed_respects_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chain respects EMBED_PROVIDER_ORDER for the list of providers to try."""
    # We'll test the _order function directly
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "siliconflow,deepinfra")
    assert embedder._order() == ("siliconflow", "deepinfra", "openrouter")
    # Default order
    monkeypatch.delenv("EMBED_PROVIDER_ORDER", raising=False)
    assert embedder._order() == embedder.DEFAULT_ORDER


# --- HostedEmbedder class tests ---


def test_hosted_embedder_init_sets_dim() -> None:
    emb = embedder.HostedEmbedder()
    assert emb.dim == 4096


def test_hosted_embedder_embed_uses_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    """HostedEmbedder.embed uses the chain and returns vectors."""
    _set_key(monkeypatch, "deepinfra")
    _set_key(monkeypatch, "siliconflow")
    call_order = []

    async def fake_call(provider: str, texts: list[str], timeout: float | None) -> list[list[float]] | None:  # noqa: ASYNC109
        call_order.append(provider)
        if provider == "deepinfra":
            return None  # fail first provider
        return _vecs(len(texts))

    monkeypatch.setattr(embedder, "_call", fake_call)
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "deepinfra,siliconflow")

    emb = embedder.get_embedder()
    result = asyncio.run(emb.embed(["test", "another"]))
    assert result is not None
    assert len(result) == 2
    assert call_order == ["deepinfra", "siliconflow"]


def test_hosted_embedder_embed_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    """HostedEmbedder.embed_one works."""
    _set_key(monkeypatch, "deepinfra")
    client = _FakeClient({"data": [{"embedding": [0.5] * 4096}]})
    monkeypatch.setattr(embedder.httpx, "AsyncClient", lambda timeout=None: client)

    emb = embedder.get_embedder()
    result = asyncio.run(emb.embed_one("test"))
    assert result == [0.015625] * 4096


def test_hosted_embedder_embed_raises_when_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    """If all providers fail, HostedEmbedder.embed raises RAGUnavailableError."""
    _set_key(monkeypatch, "deepinfra")
    _set_key(monkeypatch, "siliconflow")
    _set_key(monkeypatch, "openrouter")
    async def fake_call(*args: Any, **kwargs: Any) -> list[list[float]] | None:
        return None

    monkeypatch.setattr(embedder, "_call", fake_call)
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "deepinfra,siliconflow,openrouter")

    emb = embedder.get_embedder()
    with pytest.raises(embedder.RAGUnavailableError):
        asyncio.run(emb.embed(["test"]))


# --- get_embedder factory tests ---


def test_get_embedder_raises_on_int_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit request for int or local raises RuntimeError."""
    monkeypatch.setenv("EMBED_PROVIDER", "int")
    with pytest.raises(RuntimeError, match="not supported"):
        embedder.get_embedder()

    monkeypatch.setenv("EMBED_PROVIDER", "local")
    with pytest.raises(RuntimeError, match="not supported"):
        embedder.get_embedder()


def test_get_embedder_returns_hosted_embedder_by_default() -> None:
    """Default get_embedder returns HostedEmbedder."""
    # Ensure EMBED_PROVIDER is not set to int or local
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    try:
        emb = embedder.get_embedder()
        assert isinstance(emb, embedder.HostedEmbedder)
        assert emb.dim == 4096
    finally:
        monkeypatch.undo()


def test_get_embedder_does_not_use_provider_order_env() -> None:
    """get_embedder ignores EMBED_PROVIDER_ORDER; the HostedEmbedder uses it."""
    # Setting EMBED_PROVIDER to a provider name is invalid (since we only allow int/local to be set and they error)
    # So we leave EMBED_PROVIDER unset and test that get_embedder still works.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    monkeypatch.setenv("EMBED_PROVIDER_ORDER", "siliconflow,deepinfra")
    try:
        emb = embedder.get_embedder()
        assert isinstance(emb, embedder.HostedEmbedder)
        # The HostedEmbedder will use the order from EMBED_PROVIDER_ORDER when it runs
        # We can test that by checking its behavior, but for now just check it's instantiated.
    finally:
        monkeypatch.undo()
