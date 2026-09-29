"""Regression tests for three faults that made the retrieval ratchet lie.

Each test pins a defect that produced a WRONG number or a wrong verdict while
looking like a working monitor. They are written to fail loudly if the fix is
reverted, because the failure mode of this machinery is not crashing — it is
reporting confident numbers that mean nothing.

1. dense layer called store.search(collection, vector) — the arguments are
   (query_embedding, collection), so a list was passed where a name is read.
2. measure() passed measured=/errors= twice into LayerMetrics.
3. the per-result metrics were the ID-scoring family, so nDCG could exceed 1.0.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from rugbrain.eval import dim_preflight
from rugbrain.eval.baseline_ratchet import LayerMetrics
from rugbrain.eval.ndcg import ndcg_at_k
from rugbrain.eval.ratchet_runner import _score_hits, recall_from_gains

HITS_ONE_RELEVANT = [
    {"id": "gold-1"},
    {"id": "b"},
    {"id": "c"},
    {"id": "d"},
    {"id": "e"},
]
ROW = {"query": "q", "collection": "c", "relevant_doc_ids": ["gold-1"]}


class TestDenseSearchArgumentOrder:
    """The dense layer must pass the embedding where the embedding goes."""

    @pytest.mark.asyncio
    async def test_dense_passes_embedding_first(self, monkeypatch: Any) -> None:
        from rugbrain.eval import ratchet_runner

        captured: dict[str, Any] = {}

        class FakeStore:
            async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
                captured.update(kwargs)
                return [{"id": "gold-1"}]

            def hybrid_search(self, **kwargs: Any) -> Any:  # pragma: no cover
                raise AssertionError("hybrid must not run in the dense-only test")

        def fake_store_factory() -> Any:
            return FakeStore()

        monkeypatch.setattr("rugbrain.qdrant_store.store.QdrantVectorStore", fake_store_factory)
        monkeypatch.setattr(
            "rugbrain.embeddings.get_query_embedding",
            _fake_embedding,
        )
        await ratchet_runner.measure([ROW], layers=("dense",))

        assert captured["collection"] == "c", "collection must arrive as the name"
        assert isinstance(captured["query_embedding"], list), (
            "query_embedding must arrive as the vector list; passing them "
            "reversed makes resolve_collection call .endswith() on a list"
        )

    @pytest.mark.asyncio
    async def test_measure_survives_a_layer(self) -> None:
        from rugbrain.eval import ratchet_runner

        m = await ratchet_runner.measure([ROW], layers=())
        assert m.case_count == 1
        assert m.corpus_hash


async def _fake_embedding(text: str) -> list[float]:
    return [0.0] * 8


class TestLayerMetricsKwargs:
    """measure() must not pass measured=/errors= twice."""

    @pytest.mark.asyncio
    async def test_measure_does_not_duplicate_count_kwargs(self, monkeypatch: Any) -> None:
        """The real defect needs a real layer, not an empty one."""
        from rugbrain.eval import ratchet_runner

        class FakeStore:
            async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
                return [{"id": "gold-1"}]

        monkeypatch.setattr("rugbrain.qdrant_store.store.QdrantVectorStore", lambda: FakeStore())
        monkeypatch.setattr("rugbrain.embeddings.get_query_embedding", _fake_embedding)

        m = await ratchet_runner.measure([ROW], layers=("dense",))
        dense = m.layers["dense"]
        assert isinstance(dense, LayerMetrics)
        assert dense.measured == 1
        assert dense.errors == 0
        assert 0.0 <= dense.ndcg_at_10 <= 1.0

    def test_as_dict_already_carries_counts(self) -> None:
        d = LayerMetrics(measured=3, errors=1).as_dict()
        assert d["measured"] == 3
        assert d["errors"] == 1


class TestMetricsAreBounded:
    """A metric outside [0, 1] is a broken metric, not a good score."""

    def test_one_relevant_at_rank_one_is_perfect(self) -> None:
        scores = _score_hits(ROW, HITS_ONE_RELEVANT, (5, 10))
        assert scores["mrr"] == pytest.approx(1.0)
        assert scores["ndcg_at_10"] == pytest.approx(1.0)
        assert scores["recall_at_5"] == pytest.approx(0.2)

    def test_all_metrics_bounded_zero_to_one(self) -> None:
        gains = [0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 0, 0]
        for i, gold in enumerate(["x", "y", "z"]):
            row = {"query": "q", "collection": "c", "relevant_doc_ids": [gold]}
            hits = [{"id": gold if g else f"f{i}{j}"} for j, g in enumerate(gains)]
            for name, v in _score_hits(row, hits, (5, 10)).items():
                assert 0.0 <= v <= 1.0, f"{name}={v} out of range for gold={gold}"

    def test_id_family_ndcg_is_the_defect_we_avoided(self) -> None:
        """The old call shape returns >1.0 — this is the regression witness.

        Passing the boolean list as both retrieved and relevant makes IDCG
        use the retrieved length, so the "normalized" score exceeds 1.0. If
        this ever stops exceeding 1.0 the upstream metric changed and this
        witness needs revisiting — it is documentation, not a live gate.
        """
        booleans = [True, False, False, False, False]
        assert ndcg_at_k(booleans, booleans, 10) > 1.0

    def test_recall_from_gains_cannot_exceed_one(self) -> None:
        assert recall_from_gains([1, 1, 1, 1, 1], 5) == pytest.approx(1.0)
        assert recall_from_gains([], 5) == 0.0
        assert recall_from_gains([0, 0], 0) == 0.0

    def test_no_relevant_hit_scores_zero_not_perfect(self) -> None:
        scores = _score_hits(ROW, [{"id": f"n{i}"} for i in range(5)], (5, 10))
        assert scores["mrr"] == 0.0
        assert scores["ndcg_at_10"] == 0.0
        assert scores["recall_at_5"] == 0.0


class TestPreflightRespectsDeclaredPins:
    """A deliberate narrow pin is not a dimension split."""

    def test_declared_dim_reads_the_repo_table(self) -> None:
        assert dim_preflight.declared_dim("defi_exploits_8b") == 4096
        assert dim_preflight.declared_dim("rug_events") == 2560
        assert dim_preflight.declared_dim("wallet_clusters") == 1024

    @pytest.mark.asyncio
    async def test_pinned_2560_is_ok_and_real_split_is_not(self, monkeypatch: Any) -> None:
        async def fake_embed(text: str = "") -> tuple[int, str]:
            return 4096, "fake"

        async def fake_fetch(collection: str, url: str = "") -> tuple[int, str]:
            return 2560, "config.params.vectors.size (unnamed)"

        monkeypatch.setattr(dim_preflight, "embed_probe", fake_embed)
        monkeypatch.setattr(dim_preflight, "fetch_collection_dim", fake_fetch)

        pinned = await dim_preflight.preflight(["rug_events"])
        assert pinned[0].ok, "declared pin must not be reported as a split"

        split = await dim_preflight.preflight(["defi_exploits_8b"])
        assert not split[0].ok, "declared 4096 vs store 2560 IS a split"
        assert "DIMENSION SPLIT" in split[0].detail


class TestRerankLayerIsNotAnAliasOfHybrid:
    """The rerank layer called `rerank_mod.rerank` — a symbol that does not exist.

    `search.rerank` exports `rerank_chunks`; it has no `rerank` function. The
    call raised AttributeError on every query and a bare
    `except Exception: return hits` turned that into "return the hybrid order
    unchanged". The rerank layer was therefore a byte-for-byte copy of the
    hybrid layer, and the recorded baseline attributed two names to one order.

    A third defect hid behind it: `rerank_chunks` defaults to `keep=5`, so a
    40-document pool came back with 5 documents and the length guard rejected
    it as a shape mismatch — again identical to a no-op.
    """

    POOL: ClassVar[list[dict[str, Any]]] = [
        {"id": str(i), "content": f"document {i}", "score": 1.0 - i * 0.01} for i in range(40)
    ]

    def test_rerank_module_has_no_bare_rerank_symbol(self) -> None:
        """Pins the root cause: the name the ratchet called does not exist."""
        from rugbrain.search import rerank as rerank_mod

        assert not hasattr(rerank_mod, "rerank"), (
            "search.rerank re-introduced a bare `rerank` symbol; the ratchet "
            "call site must be revisited"
        )
        assert callable(rerank_mod.rerank_chunks)

    @pytest.mark.asyncio
    async def test_length_is_preserved_not_truncated_to_default_keep(
        self, monkeypatch: Any
    ) -> None:
        from rugbrain.eval import ratchet_retrievers as rr

        seen: dict[str, int] = {}

        async def fake_rerank_chunks(
            query: str, pool: list[dict[str, Any]], top_n: int = 20, keep: int = 5, **_: Any
        ) -> list[dict[str, Any]]:
            seen["top_n"] = top_n
            seen["keep"] = keep
            return [dict(h, rerank_method="fake", reranked=True) for h in pool]

        monkeypatch.setattr("rugbrain.search.rerank.rerank_chunks", fake_rerank_chunks)
        out = await rr._rerank("q", self.POOL, 40)

        assert seen["top_n"] == 40 and seen["keep"] == 40, (
            "top_n/keep must be pinned to the pool size or the default keep=5 "
            "truncates the pool and looks like a no-op"
        )
        assert len(out) == len(self.POOL)

    @pytest.mark.asyncio
    async def test_disabled_reranker_raises_instead_of_passing_through(
        self, monkeypatch: Any
    ) -> None:
        from rugbrain.eval import ratchet_retrievers as rr
        from rugbrain.search import rerank as rerank_mod

        monkeypatch.setattr(rerank_mod, "RERANKER_ENABLED", False)
        with pytest.raises(rr.RerankUnavailableError, match="disabled"):
            await rr._rerank("q", self.POOL, 40)

    @pytest.mark.asyncio
    async def test_module_noop_shape_raises(self, monkeypatch: Any) -> None:
        """`rerank_method="none"` is production's graceful no-op, not a rerank."""
        from rugbrain.eval import ratchet_retrievers as rr

        async def fake_noop(
            query: str, pool: list[dict[str, Any]], **_: Any
        ) -> list[dict[str, Any]]:
            return [dict(h, rerank_method="none", reranked=False) for h in pool]

        monkeypatch.setattr("rugbrain.search.rerank.rerank_chunks", fake_noop)
        with pytest.raises(rr.RerankUnavailableError, match="rerank_method=none"):
            await rr._rerank("q", self.POOL, 40)

    @pytest.mark.asyncio
    async def test_transport_failure_raises_not_swallows(self, monkeypatch: Any) -> None:
        from rugbrain.eval import ratchet_retrievers as rr

        async def boom(query: str, pool: list[dict[str, Any]], **_: Any) -> list[dict[str, Any]]:
            raise RuntimeError("deepinfra 500")

        monkeypatch.setattr("rugbrain.search.rerank.rerank_chunks", boom)
        with pytest.raises(rr.RerankUnavailableError, match="rerank call failed"):
            await rr._rerank("q", self.POOL, 40)

    @pytest.mark.asyncio
    async def test_length_mismatch_raises(self, monkeypatch: Any) -> None:
        from rugbrain.eval import ratchet_retrievers as rr

        async def fake_short(
            query: str, pool: list[dict[str, Any]], **_: Any
        ) -> list[dict[str, Any]]:
            return pool[:3]

        monkeypatch.setattr("rugbrain.search.rerank.rerank_chunks", fake_short)
        with pytest.raises(rr.RerankUnavailableError, match="expected 40"):
            await rr._rerank("q", self.POOL, 40)

    @pytest.mark.asyncio
    async def test_rerank_actually_reorders_relative_to_input(self, monkeypatch: Any) -> None:
        """A layer that cannot change the order is not measuring a rerank.

        This is the case the bug made unrepresentable: the cross-encoder puts
        the relevant document first even though retrieval ranked it last.
        """
        from rugbrain.eval import ratchet_retrievers as rr

        pool = [
            {"id": "a", "content": "nice weather in the city", "score": 1.0},
            {"id": "b", "content": "python tutorial for beginners", "score": 0.9},
            {"id": "c", "content": "flash loan drained the pool", "score": 0.5},
            {"id": "d", "content": "reentrancy lets an attacker drain funds", "score": 0.1},
        ]

        async def fake_rerank_chunks(
            query: str, chunks: list[dict[str, Any]], top_n: int = 20, keep: int = 5, **_: Any
        ) -> list[dict[str, Any]]:
            out = [dict(c) for c in reversed(chunks)]  # cross-encoder disagrees
            for c in out:
                c["rerank_method"] = "fake"
                c["reranked"] = True
            return out

        monkeypatch.setattr("rugbrain.search.rerank.rerank_chunks", fake_rerank_chunks)
        out = await rr._rerank("reentrancy drain", pool, 4)
        assert [h["id"] for h in out] != [h["id"] for h in pool]
        assert out[0]["id"] == "d"

    def test_method_is_attributable(self) -> None:
        from rugbrain.eval.ratchet_retrievers import rerank_method_of

        assert rerank_method_of([]) == "none"
        assert rerank_method_of([{"id": "a"}]) == "none"
        assert rerank_method_of([{"id": "a", "rerank_method": "deepinfra:x"}]) == "deepinfra:x"
