"""Tests for the extracted reranker siblings.

The three extractions from ``rugbrain.reranker`` are each one job, so each
gets direct coverage of the behaviour that used to be only implicitly
exercised through the facade.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from rugbrain import rerank_compat, rerank_health, rerank_instructions


class FakeResp:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, resp: FakeResp, seen: list | None = None) -> None:
        self._resp = resp
        self._seen = seen

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str) -> FakeResp:
        if self._seen is not None:
            self._seen.append(("GET", url))
        return self._resp

    async def post(self, url: str, json: dict | None = None) -> FakeResp:
        if self._seen is not None:
            self._seen.append(("POST", url, json))
        return self._resp


class TestHealthCache:
    def test_cache_is_the_module_owned_mutable_dict(self) -> None:
        assert rerank_health.cache() is rerank_health._health

    def test_cache_shape(self) -> None:
        assert set(rerank_health.cache()) == {"ok", "last_check", "latency_ms"}

    async def test_fast_health_trusts_fresh_ok(self) -> None:
        await rerank_health._mark(True, 12.0)
        assert await rerank_health._fast_health() is True
        assert rerank_health.cache()["latency_ms"] == 12.0

    async def test_fast_health_trusts_fresh_down(self) -> None:
        await rerank_health._mark(False)
        assert await rerank_health._fast_health() is False

    async def test_fast_health_probes_when_stale(self) -> None:
        await rerank_health._mark(False)
        # Backdate past the 60s "recently down" window -> allow a real probe.
        rerank_health.cache()["last_check"] = time.time() - 3600
        assert await rerank_health._fast_health() is True

    async def test_mark_records_latency_once(self) -> None:
        await rerank_health._mark(True, 7.4)
        first = rerank_health.cache()["latency_ms"]
        await rerank_health._mark(True)
        # A reachability-only update must not clobber the last measured latency.
        assert rerank_health.cache()["latency_ms"] == first


class TestHealthProbe:
    async def test_probe_ok_updates_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list = []
        monkeypatch.setattr(rerank_health, "enabled_getter", lambda: True)
        monkeypatch.setattr(
            rerank_health.httpx,
            "AsyncClient",
            lambda **k: FakeClient(FakeResp(200, {"model": "qwen3-8b"}), seen),
        )
        out = await rerank_health.get_reranker_health()
        assert out["ok"] is True
        assert out["model"] == "qwen3-8b"
        assert rerank_health.cache()["ok"] is True
        assert seen[0][1].endswith("/health")

    async def test_probe_disabled_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_health, "enabled_getter", lambda: False)
        out = await rerank_health.get_reranker_health()
        assert out == {"ok": False, "enabled": False, "reason": "RERANKER_ENABLED=false"}

    async def test_probe_non_200_marks_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_health, "enabled_getter", lambda: True)
        monkeypatch.setattr(
            rerank_health.httpx, "AsyncClient", lambda **k: FakeClient(FakeResp(503))
        )
        out = await rerank_health.get_reranker_health()
        assert out["ok"] is False
        assert out["status_code"] == 503
        assert rerank_health.cache()["ok"] is False

    async def test_probe_connect_error_marks_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_health, "enabled_getter", lambda: True)

        class Boom:
            async def __aenter__(self) -> Boom:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def get(self, url: str) -> FakeResp:
                raise rerank_health.httpx.ConnectError("down")

        monkeypatch.setattr(rerank_health.httpx, "AsyncClient", lambda **k: Boom())
        out = await rerank_health.get_reranker_health()
        assert out["ok"] is False
        assert "error" in out
        assert rerank_health.cache()["ok"] is False


class TestInstructionAttach:
    def test_maps_indices_onto_source_items(self) -> None:
        items = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
        results = [{"index": 2, "relevance_score": 0.1}, {"index": 0, "relevance_score": 0.9}]
        out = rerank_instructions._attach_scores(items, results, "_s")
        assert [x["title"] for x in out] == ["a", "c"]
        assert out[0]["_s"] == 0.9

    def test_out_of_range_index_is_dropped(self) -> None:
        items = [{"title": "a"}]
        results = [{"index": 7, "relevance_score": 0.5}]
        assert rerank_instructions._attach_scores(items, results, "_s") == []

    def test_does_not_mutate_source_items(self) -> None:
        items = [{"title": "a"}]
        rerank_instructions._attach_scores(items, [{"index": 0, "relevance_score": 0.5}], "_s")
        assert items == [{"title": "a"}]


class TestInstructionLibrary:
    def test_every_task_type_has_an_instruction(self) -> None:
        assert set(rerank_instructions.RERANK_INSTRUCTIONS) == {
            "content_digest",
            "alert_filter",
            "kol_scam",
            "search",
            "wallet_risk",
        }

    async def test_instructed_prefixes_known_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict = {}

        async def fake_rerank(query: str, docs: list, top_k: int = 10) -> list:
            seen["query"] = query
            seen["top_k"] = top_k
            return []

        import rugbrain.reranker as rr

        monkeypatch.setattr(rr, "rerank", fake_rerank)
        await rerank_instructions.rerank_instructed("kol_scam", "is this a scam?", ["a"], top_k=3)
        assert seen["query"].startswith("[INST] ")
        assert "is this a scam?" in seen["query"]
        assert seen["top_k"] == 3

    async def test_instructed_passes_unknown_task_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict = {}

        async def fake_rerank(query: str, docs: list, top_k: int = 10) -> list:
            seen["query"] = query
            return []

        import rugbrain.reranker as rr

        monkeypatch.setattr(rr, "rerank", fake_rerank)
        await rerank_instructions.rerank_instructed("nope", "plain query", ["a"])
        assert seen["query"] == "plain query"

    async def test_alerts_empty_short_circuits(self) -> None:
        assert await rerank_instructions.rerank_alerts([]) == []

    async def test_alerts_tag_and_sort_by_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_instructed(task_type: str, query: str, docs: list, top_k: int = 10) -> list:
            assert task_type == "alert_filter"
            return [{"index": 1, "relevance_score": 0.2}, {"index": 0, "relevance_score": 0.8}]

        monkeypatch.setattr(rerank_instructions, "rerank_instructed", fake_instructed)
        out = await rerank_instructions.rerank_alerts(
            [{"title": "low", "message": "x"}, {"title": "high", "message": "y"}]
        )
        # index 0 ("low") scored 0.8, index 1 ("high") scored 0.2.
        assert [a["title"] for a in out] == ["low", "high"]
        assert out[0]["_rerank_score"] == 0.8

    async def test_kol_tweets_use_scam_score_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_instructed(task_type: str, query: str, docs: list, top_k: int = 10) -> list:
            assert task_type == "kol_scam"
            return [{"index": 0, "relevance_score": 0.4}]

        monkeypatch.setattr(rerank_instructions, "rerank_instructed", fake_instructed)
        out = await rerank_instructions.rerank_kol_tweets([{"text": "moon", "author": "k"}])
        assert out[0]["_scam_score"] == 0.4

    async def test_kol_tweets_empty_short_circuits(self) -> None:
        assert await rerank_instructions.rerank_kol_tweets([]) == []


class TestCompatSurface:
    def test_class_honours_explicit_url(self) -> None:
        assert rerank_compat.Reranker("http://x:1")._url == "http://x:1"

    def test_class_defaults_to_env_url(self) -> None:
        assert rerank_compat.Reranker()._url == rerank_compat.RERANKER_URL

    def test_get_reranker_returns_instance(self) -> None:
        assert isinstance(rerank_compat.get_reranker(), rerank_compat.Reranker)

    async def test_tiered_disabled_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_compat, "RERANKER_ENABLED", False)
        assert await rerank_compat.rerank_tiered("q", ["a"]) == []

    async def test_tiered_empty_docs_returns_empty(self) -> None:
        assert await rerank_compat.rerank_tiered("q", []) == []

    async def test_tiered_sorts_and_truncates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "results": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]
        }
        monkeypatch.setattr(
            rerank_compat.httpx, "AsyncClient", lambda **k: FakeClient(FakeResp(200, payload))
        )
        out = await rerank_compat.rerank_tiered("q", ["a", "b"], top_k=1)
        assert [r["index"] for r in out] == [1]

    async def test_tiered_raw_returns_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "results": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]
        }
        monkeypatch.setattr(
            rerank_compat.httpx, "AsyncClient", lambda **k: FakeClient(FakeResp(200, payload))
        )
        out = await rerank_compat.rerank_tiered("q", ["a", "b"], top_k=1, raw=True)
        assert len(out) == 2

    async def test_tiered_non_200_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            rerank_compat.httpx, "AsyncClient", lambda **k: FakeClient(FakeResp(500))
        )
        assert await rerank_compat.rerank_tiered("q", ["a"]) == []

    async def test_tiered_transport_error_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Boom:
            async def __aenter__(self) -> Boom:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def post(self, url: str, json: dict | None = None) -> FakeResp:
                raise rerank_compat.httpx.ConnectError("nope")

        monkeypatch.setattr(rerank_compat.httpx, "AsyncClient", lambda **k: Boom())
        assert await rerank_compat.rerank_tiered("q", ["a"]) == []

    async def test_class_rerank_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import rugbrain.reranker as rr

        async def fake_rerank(query: str, docs: list, top_k: int = 10) -> list:
            return [{"index": 0, "relevance_score": 1.0}]

        monkeypatch.setattr(rr, "rerank", fake_rerank)
        out = await rerank_compat.Reranker().rerank("q", ["a"], top_k=2)
        assert out[0]["relevance_score"] == 1.0

    async def test_class_health_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_compat, "RERANKER_ENABLED", False)
        assert await rerank_compat.Reranker().health() == {"ok": False, "enabled": False}

    async def test_class_health_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_compat, "RERANKER_ENABLED", True)
        monkeypatch.setattr(
            rerank_compat.httpx,
            "AsyncClient",
            lambda **k: FakeClient(FakeResp(200, {"model": "m"})),
        )
        assert (await rerank_compat.Reranker().health())["model"] == "m"

    async def test_class_health_non_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_compat, "RERANKER_ENABLED", True)
        monkeypatch.setattr(
            rerank_compat.httpx, "AsyncClient", lambda **k: FakeClient(FakeResp(502))
        )
        assert (await rerank_compat.Reranker().health())["ok"] is False

    async def test_class_health_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rerank_compat, "RERANKER_ENABLED", True)

        class Boom:
            async def __aenter__(self) -> Boom:
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def get(self, url: str) -> FakeResp:
                raise rerank_compat.httpx.ConnectError("down")

        monkeypatch.setattr(rerank_compat.httpx, "AsyncClient", lambda **k: Boom())
        assert (await rerank_compat.Reranker().health())["ok"] is False

    async def test_health_loop_sleeps_forever(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        async def fake_probe() -> dict:
            calls.append(1)
            return {"ok": True}

        async def fake_sleep(_seconds: float) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(rerank_health, "get_reranker_health", fake_probe)
        monkeypatch.setattr(rerank_health.asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            await rerank_health.reranker_health_loop()
        assert calls == [1]
