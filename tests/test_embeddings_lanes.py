"""Contracts for the extracted embedding lanes (``rugbrain.embeddings_pkg``).

These pin the properties that made the split safe, so a future refactor cannot
quietly reintroduce the bug class this area is famous for:

* a lane returns ``None`` to fall through — never a placeholder vector;
* a lane that is patched on ``embeddings`` is the lane the CHAIN runs (no
  import-time snapshot that silently ignores a monkeypatch);
* a dim mismatch REJECTS instead of being padded or truncated into the wrong
  vector space.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rugbrain import embeddings as emb
from rugbrain.embeddings_pkg import local_lane
from rugbrain.embeddings_pkg.normalize import resize, truncate_mrl


class TestNormalize:
    def test_resize_unit_length(self) -> None:
        out = resize([3.0, 4.0], 2)
        assert out == pytest.approx([0.6, 0.8])

    def test_resize_rejects_wide_vector(self) -> None:
        """A provider returning MORE than the target must not be silently cut."""
        with pytest.raises(ValueError, match="dim mismatch"):
            resize([0.1] * 4096, 2560)

    def test_resize_rejects_narrow_vector(self) -> None:
        """Padding a 1024d vector into a 2560d index poisons retrieval."""
        with pytest.raises(ValueError, match="dim mismatch"):
            resize([0.1] * 1024, 2560)

    def test_resize_zero_vector_is_not_divided(self) -> None:
        assert resize([0.0, 0.0], 2) == [0.0, 0.0]

    def test_truncate_mrl_narrows_and_renormalises(self) -> None:
        out = truncate_mrl([3.0, 4.0, 99.0], 2)
        assert out == pytest.approx([0.6, 0.8])

    def test_truncate_mrl_refuses_too_narrow(self) -> None:
        """None, not a pad: the lane chain falls through and fails loud."""
        assert truncate_mrl([1.0, 2.0], 4096) is None


class TestLaneResolution:
    """The chain must resolve lanes through module globals at CALL time."""

    def test_lane_globals_all_bound(self) -> None:
        for lane, name in emb._LANE_GLOBALS.items():
            assert callable(emb.__dict__.get(name)), f"{lane} -> {name} unbound"

    async def test_chain_honours_monkeypatched_lane(self, monkeypatch: Any) -> None:
        """Patching `embeddings._deepinfra_embed` must change what runs.

        An import-time snapshot would make this assertion fail — which is the
        whole point: a test that patches the wrong name passes against the
        REAL provider and reports a green suite for a dead lane.
        """
        seen: list[str] = []

        async def _fake(text: str, **_: Any) -> list[float]:
            seen.append(text)
            return [0.5] * emb.EMBEDDING_DIM

        monkeypatch.setattr(emb, "_BACKEND", "deepinfra")
        monkeypatch.setattr(emb, "_deepinfra_embed", _fake)
        out = await emb.get_embedding("hello")
        assert seen == ["hello"]
        assert len(out) == emb.EMBEDDING_DIM

    async def test_unknown_lane_falls_through_to_fail_loud(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(emb, "_BACKEND", "auto")
        monkeypatch.setattr(emb, "_deepinfra_embed", None)
        monkeypatch.setattr(emb, "_int_reachable", lambda: False)
        with pytest.raises(emb.RAGUnavailableError):
            await emb.get_embedding("hello")

    def test_legacy_lane_called_without_query_kwarg(self, monkeypatch: Any) -> None:
        """Legacy rungs take (text, dim) only; the chain still reaches them."""
        called: list[tuple[Any, ...]] = []

        def _legacy(text: str, dim: int = 0) -> list[float]:
            called.append((text, dim))
            return [0.1] * 4

        # monkeypatch, not __dict__ + del: overwriting the real
        # _gateway_embed and deleting it afterwards leaves the module WITHOUT
        # the lane, and every later test that needs it fails with an
        # AttributeError that looks nothing like this test.
        monkeypatch.setattr(emb, "_gateway_embed", _legacy)
        out = asyncio.run(emb._call_lane("gateway", "q", False, None))
        assert out == [0.1] * 4
        assert called == [("q", 0)]


class TestLocalProbe:
    def test_probe_result_is_cached(self, monkeypatch: Any) -> None:
        """One probe per TTL, not one per query — and a cold lane retries."""
        monkeypatch.delenv("RAG_EMBED_NO_CACHE", raising=False)
        monkeypatch.setenv("_QWEN_PROBED", "1")
        monkeypatch.setenv("_QWEN_OK", "0")
        monkeypatch.setenv("_QWEN_PROBED_AT", "1")  # ancient -> expired
        calls: list[int] = []

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                calls.append(1)

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def get(self, url: str) -> Any:
                raise OSError("down")

        monkeypatch.setattr(local_lane.httpx, "Client", _Client)
        assert local_lane.reachable() is False
        assert local_lane.reachable() is False  # served from cache
        assert len(calls) == 1, "second call must not re-probe inside the TTL"

    def test_no_cache_env_forces_fresh_probe(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("RAG_EMBED_NO_CACHE", "1")
        monkeypatch.setenv("_QWEN_PROBED", "1")
        monkeypatch.setenv("_QWEN_OK", "0")
        monkeypatch.setenv("_QWEN_PROBED_AT", "1")
        calls: list[int] = []

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                calls.append(1)

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def get(self, url: str) -> Any:
                raise OSError("down")

        monkeypatch.setattr(local_lane.httpx, "Client", _Client)
        assert local_lane.reachable() is False
        assert local_lane.reachable() is False
        assert len(calls) == 2, "RAG_EMBED_NO_CACHE must force a fresh probe"
