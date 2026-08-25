"""Tests for multi_embedder: same-space fallback chain + vector landscape.

Covers the 2026-08-25 multi-vector awareness work:
- embed() falls back local qwen4b -> OpenRouter same-model (same space)
  when local is saturated, and does NOT fall back across model families
- landscape() reads ACTUAL dims from qdrant, infers backends, flags
  mismatches between reality and configuration
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rugbrain.multi_embedder import SAME_SPACE_FALLBACKS, MultiEmbedder


@pytest.fixture
def me() -> MultiEmbedder:
    return MultiEmbedder()


class TestSameSpaceFallbacks:
    def test_qwen4b_falls_back_to_or_qwen4b_only(self):
        chain = SAME_SPACE_FALLBACKS["qwen4b"]
        assert chain == ("or_qwen4b",)

    def test_no_cross_family_fallback(self):
        """nemotron must NEVER fall back to qwen (different semantic space)."""
        assert "nemotron" not in SAME_SPACE_FALLBACKS
        assert "qwen06b" not in SAME_SPACE_FALLBACKS

    def test_or_qwen4b_is_same_model(self, me: MultiEmbedder):
        """The remote fallback must serve the SAME model as local qwen4b."""
        local = me.backends["qwen4b"]
        remote = me.backends["or_qwen4b"]
        assert local.model.split("/")[-1] == remote.model.split("/")[-1]
        assert remote.dim == local.dim

    @pytest.mark.asyncio
    async def test_embed_falls_back_when_local_fails(self, me: MultiEmbedder):
        calls: list[str] = []

        async def fake_once(text: str, be):
            calls.append(be.name)
            if be.name == "qwen4b":
                raise RuntimeError("local saturated")
            return [0.1] * be.dim

        with patch.object(me, "_embed_once", side_effect=fake_once):
            vec = await me.embed("query", "qwen4b")
        assert calls == ["qwen4b", "or_qwen4b"]
        assert len(vec) == me.backends["or_qwen4b"].dim

    @pytest.mark.asyncio
    async def test_embed_disabled_fallback_raises(self, me: MultiEmbedder):
        async def fake_once(text: str, be):
            raise RuntimeError("down")

        with (
            patch.dict("os.environ", {"RAG_QUERY_REMOTE_FALLBACK": "0"}),
            patch.object(me, "_embed_once", side_effect=fake_once),
            pytest.raises(RuntimeError, match="all embed backends failed"),
        ):
            await me.embed("query", "qwen4b")

    @pytest.mark.asyncio
    async def test_embed_no_fallback_on_success(self, me: MultiEmbedder):
        calls: list[str] = []

        async def fake_once(text: str, be):
            calls.append(be.name)
            return [0.2] * be.dim

        with patch.object(me, "_embed_once", side_effect=fake_once):
            await me.embed("query", "qwen4b")
        assert calls == ["qwen4b"]


class TestLandscape:
    @pytest.mark.asyncio
    async def test_landscape_reads_actual_dims_and_flags_mismatch(self, me: MultiEmbedder):
        class FakeResp:
            def __init__(self, data):
                self._d = data

            def json(self):
                return self._d

        async def fake_get(self, url, **kw):
            if url.endswith("/collections"):
                return FakeResp(
                    {"result": {"collections": [{"name": "wallet_v2"}, {"name": "news_v2_i"}]}}
                )
            if url.endswith("/collections/wallet_v2"):
                return FakeResp(
                    {
                        "result": {
                            "config": {"params": {"vectors": {"dense": {"size": 2560}}}},
                            "points_count": 100,
                        }
                    }
                )
            if url.endswith("/collections/news_v2_i"):
                return FakeResp(
                    {
                        "result": {
                            "config": {"params": {"vectors": {"size": 2048}}},
                            "points_count": 5,
                        }
                    }
                )
            raise AssertionError(url)

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = await me.landscape(["http://qdrant-test:6333"])

        assert out["total_points"] == 105
        w = out["instances"]["http://qdrant-test:6333"]["wallet_v2"]
        n = out["instances"]["http://qdrant-test:6333"]["news_v2_i"]
        assert w["dim"] == 2560
        assert w["inferred_backend"] == "qwen4b"
        assert n["dim"] == 2048
        assert n["inferred_backend"] == "nemotron"
        # default resolution for unmapped collections is qwen4b@registry-dim;
        # news_v2_i at 2048 must surface as a MISMATCH against that.
        assert any(m["collection"] == "news_v2_i" for m in out["mismatches"])

    @pytest.mark.asyncio
    async def test_landscape_survives_instance_down(self, me: MultiEmbedder):
        async def fake_get(self, url, **kw):
            raise ConnectionError("refused")

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = await me.landscape(["http://dead:6333"])
        assert "_error" in out["instances"]["http://dead:6333"]
