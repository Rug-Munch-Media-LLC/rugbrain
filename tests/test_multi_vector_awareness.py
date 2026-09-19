"""Tests for multi_embedder: embed chain + vector landscape.

Covers the 2026-08-29 embed consolidation:
- embed() walks the EMBED_CHAINS provider chain — DeepInfra primary,
  local :8004 fallback for the canonical qwen4b space — and does NOT
  fall back across model families
- RAG_QUERY_REMOTE_FALLBACK=0 strips remote lanes to a fully-local chain
- landscape() reads ACTUAL dims from qdrant, infers backends, flags
  mismatches between reality and configuration
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rugbrain.multi_embedder import EMBED_CHAINS, SAME_SPACE_FALLBACKS, MultiEmbedder


@pytest.fixture
def me() -> MultiEmbedder:
    return MultiEmbedder()


class TestEmbedChains:
    def test_qwen4b_chain_is_deepinfra_then_local(self):
        """Canonical 2560 space: DeepInfra primary, local :8004 fallback."""
        chain = EMBED_CHAINS["qwen4b"]
        assert chain == ("deepinfra", "qwen4b")

    def test_deepinfra_alias_chain(self):
        """Backend-name lookups resolve to the same canonical chain."""
        assert EMBED_CHAINS["deepinfra"] == ("deepinfra", "qwen4b")

    def test_same_space_fallbacks_alias(self):
        """Deprecated pre-2026-08-29 name must keep pointing at EMBED_CHAINS."""
        assert SAME_SPACE_FALLBACKS is EMBED_CHAINS

    def test_no_cross_family_fallback(self):
        """nemotron must NEVER fall back to qwen (different semantic space)."""
        assert "nemotron" not in EMBED_CHAINS
        assert "qwen06b" not in EMBED_CHAINS

    def test_deepinfra_is_same_model_as_local(self, me: MultiEmbedder):
        """The remote primary must serve the SAME model as local qwen4b."""
        local = me.backends["qwen4b"]
        remote = me.backends["deepinfra"]
        assert local.model.split("/")[-1].lower() == remote.model.split("/")[-1].lower()
        assert remote.dim == local.dim

    @pytest.mark.asyncio
    async def test_embed_falls_back_when_remote_fails(self, me: MultiEmbedder):
        calls: list[str] = []

        async def fake_once(text: str, be, target_dim=None):
            calls.append(be.name)
            if be.name == "deepinfra":
                raise RuntimeError("remote 5xx")
            return [0.1] * be.dim

        with patch.object(me, "_embed_once", side_effect=fake_once):
            vec = await me.embed("query", "qwen4b")
        assert calls == ["deepinfra", "qwen4b"]
        assert len(vec) == me.backends["qwen4b"].dim

    @pytest.mark.asyncio
    async def test_embed_strips_remote_lanes_when_disabled(self, me: MultiEmbedder):
        """RAG_QUERY_REMOTE_FALLBACK=0 → fully-local chain (deepinfra gone)."""
        calls: list[str] = []

        async def fake_once(text: str, be, target_dim=None):
            calls.append(be.name)
            return [0.1] * be.dim

        with (
            patch.dict("os.environ", {"RAG_QUERY_REMOTE_FALLBACK": "0"}),
            patch.object(me, "_embed_once", side_effect=fake_once),
        ):
            await me.embed("query", "qwen4b")
        assert calls == ["qwen4b"]

    @pytest.mark.asyncio
    async def test_embed_disabled_fallback_raises(self, me: MultiEmbedder):
        async def fake_once(text: str, be, target_dim=None):
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

        async def fake_once(text: str, be, target_dim=None):
            calls.append(be.name)
            return [0.2] * be.dim

        with patch.object(me, "_embed_once", side_effect=fake_once):
            await me.embed("query", "qwen4b")
        assert calls == ["deepinfra"]


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
        # Dim-driven resolution (2026-08-25): landscape seeds the actual dim
        # into the cache BEFORE resolving, so news_v2_i correctly resolves
        # nemotron@2048 — NO false mismatch for unmapped collections.
        assert n["configured_backend"] == "nemotron"
        assert n["configured_dim"] == 2048
        assert not any(m["collection"] == "news_v2_i" for m in out["mismatches"])

    @pytest.mark.asyncio
    async def test_landscape_survives_instance_down(self, me: MultiEmbedder):
        async def fake_get(self, url, **kw):
            raise ConnectionError("refused")

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = await me.landscape(["http://dead:6333"])
        assert "_error" in out["instances"]["http://dead:6333"]


class TestDimDrivenResolution:
    @pytest.mark.asyncio
    async def test_unmapped_collection_resolves_by_actual_dim(self, me: MultiEmbedder):
        """A 2560 collection under a nemotron default must resolve qwen4b."""
        me.default_backend = "nemotron"  # simulate live container default
        me._dim_cache["wallet_v2"] = 2560
        be, dim = me.backend_for("wallet_v2")
        assert be.name == "qwen4b"
        assert dim == 2560

    @pytest.mark.asyncio
    async def test_2048_collection_resolves_nemotron(self, me: MultiEmbedder):
        me._dim_cache["news_v2_i"] = 2048
        be, dim = me.backend_for("news_v2_i")
        assert be.name == "nemotron"
        assert dim == 2048

    @pytest.mark.asyncio
    async def test_1024_collection_falls_to_default_lane(self, me: MultiEmbedder):
        """1024 legacy collections are retired (2026-09-14): there is no 0.6b
        lane anymore — an old 1024 collection resolves to the default backend
        so callers get a loud mismatch instead of a cross-model guess."""
        me._dim_cache["old_stuff"] = 1024
        be, _dim = me.backend_for("old_stuff")
        assert be.name == me.default_backend
        assert "qwen06b" not in me.backends

    @pytest.mark.asyncio
    async def test_resolve_collections_prewarms_cache(self, me: MultiEmbedder):
        async def fake_dim(collection: str) -> int | None:
            # mirror real actual_dim: fetch AND write the cache
            dim = {"wallet_v2": 2560}.get(collection)
            me._dim_cache[collection] = dim
            return dim

        with patch.object(me, "actual_dim", side_effect=fake_dim):
            await me.resolve_collections(["wallet_v2", "unknown_coll"])
        assert me._dim_cache["wallet_v2"] == 2560
        assert me._dim_cache["unknown_coll"] is None
        be, _ = me.backend_for("wallet_v2")
        assert be.name == "qwen4b"

    def test_qdrant_urls_accept_host_and_container_rest_ports(self):
        """REST probe list: container :6333 + host :6334 both allowed
        (2026-09-14 — host-run services serve REST on the mapped port),
        deduped, and never the gRPC service."""
        urls = MultiEmbedder._qdrant_rest_urls()
        assert urls, "default REST probe list must not be empty"
        assert all(u.endswith(":6333") or u.endswith(":6334") for u in urls)
        assert any(u.endswith(":6334") for u in urls), "host REST port must be probed"
        assert len(urls) == len(set(urls))


class TestLandscapeTrueMismatch:
    @pytest.mark.asyncio
    async def test_explicit_env_misconfig_still_flags(self):
        """An EXPLICIT wrong env map entry must still surface as MISMATCH."""
        me = MultiEmbedder()
        me.collection_map["wallet_v2"] = ("nemotron", 2048)  # wrong on purpose

        class FakeResp:
            def __init__(self, data):
                self._d = data

            def json(self):
                return self._d

        async def fake_get(self, url, **kw):
            if url.endswith("/collections"):
                return FakeResp({"result": {"collections": [{"name": "wallet_v2"}]}})
            return FakeResp(
                {
                    "result": {
                        "config": {"params": {"vectors": {"dense": {"size": 2560}}}},
                        "points_count": 100,
                    }
                }
            )

        with patch("httpx.AsyncClient.get", new=fake_get):
            out = await me.landscape(["http://qdrant-test:6333"])
        assert any(m["collection"] == "wallet_v2" for m in out["mismatches"])
        entry = out["instances"]["http://qdrant-test:6333"]["wallet_v2"]
        assert entry["MISMATCH"] is True
