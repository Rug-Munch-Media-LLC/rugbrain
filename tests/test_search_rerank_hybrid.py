"""Targeted tests for the search-lane reranker + hybrid retrieval.

Covers the two additive search-quality features added to the search lane:

  * cross-encoder reranking (``rugbrain.search.rerank.rerank_chunks``):
    self-hosted Qwen3-Reranker-8B (llama.cpp HTTP) with a graceful no-op
    fallback (server unreachable -> original ordering preserved);
  * hybrid retrieval (``hybrid=True`` on ``search_multi_collection`` /
    ``three_pillar_search``): a lightweight in-memory keyword/BM25 lane is
    RRF-fused with the vector lane, surfacing keyword-only hits.

All unit tests: no external services, no ML models required (the HTTP
scoring layer ``_score_candidates`` is monkeypatched).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import rugbrain.search.engine as engine_mod
import rugbrain.search.rerank as rerank_mod
from rugbrain.search.engine import _hybrid_fuse
from rugbrain.search.rerank import rerank_chunks

# ── helpers ──────────────────────────────────────────────────────────


def _chunks(n: int, base: float = 0.9) -> list[dict[str, Any]]:
    """n chunks with descending original ``similarity`` scores."""
    return [
        {"id": f"chunk-{i}", "content": f"chunk content {i}", "similarity": base - i * 0.001}
        for i in range(n)
    ]


class _FakeEmbedder:
    """Minimal stand-in for CryptoEmbedder (no network)."""

    async def embed_query(self, query: str) -> list[float]:
        return [0.1, 0.2, 0.3]


def _fake_score_factory(
    scores: list[float],
    *,
    method: str = "qwen3-reranker-8b",
    seen: list[list[dict[str, Any]]] | None = None,
) -> Any:
    """Build an async ``_score_candidates`` stand-in returning *scores*."""

    async def fake_score(query: str, candidates: list[dict[str, Any]]) -> tuple[list[float], str]:
        if seen is not None:
            seen.append(candidates)
        return list(scores), method

    return fake_score


# ── 1. reranker: module import + graceful no-op fallback ─────────────


def test_rerank_module_imports_cleanly() -> None:
    """No hard deps at import time (no torch/sentence-transformers)."""
    import rugbrain.search.rerank  # noqa: F401


def test_rerank_chunks_noop_fallback_orders_by_original_score(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Scorer unavailable -> docs keep original ordering, tagged 'none'."""
    monkeypatch.setattr(rerank_mod, "_score_candidates", _fake_score_factory([], method="none"))

    chunks = [
        {"id": "low", "content": "low", "similarity": 0.3},
        {"id": "high", "content": "high", "similarity": 0.9},
        {"id": "mid", "content": "mid", "similarity": 0.6},
    ]

    async def run() -> Any:
        return await rerank_chunks("some query", chunks, top_n=3, keep=2)

    out = asyncio.run(run())
    assert [r["id"] for r in out] == ["high", "mid"]
    assert all(r["rerank_method"] == "none" for r in out)
    assert all(r["reranked"] is False for r in out)


def test_rerank_chunks_returns_empty_for_empty_chunks(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def run() -> Any:
        return await rerank_chunks("query", [])

    assert asyncio.run(run()) == []


# ── 2. reranker: cross-encoder ordering ───────────────────────────────


def test_rerank_chunks_orders_by_cross_encoder_score(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Cross-encoder scores drive final ordering, not original similarity."""
    monkeypatch.setattr(
        rerank_mod, "_score_candidates", _fake_score_factory([0.1, 0.9, 0.5])
    )  # chunk-1 (by original order) wins

    chunks = [
        {"id": "a", "content": "a", "similarity": 0.99},
        {"id": "b", "content": "b", "similarity": 0.98},
        {"id": "c", "content": "c", "similarity": 0.97},
    ]

    async def run() -> Any:
        return await rerank_chunks("query", chunks, top_n=3, keep=2)

    out = asyncio.run(run())
    assert [r["id"] for r in out] == ["b", "c"]  # scores 0.9 then 0.5
    assert out[0]["rerank_score"] == pytest.approx(0.9)
    assert all(r["reranked"] is True for r in out)
    assert out[0]["rerank_method"] == "qwen3-reranker-8b"


def test_rerank_chunks_respects_top_n_candidates(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Only the top_n candidates (by original score) reach the scorer."""
    seen: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(
        rerank_mod,
        "_score_candidates",
        _fake_score_factory([float(len(_chunks(20)) - i) for i in range(20)], seen=seen),
    )

    chunks = _chunks(25)

    async def run() -> Any:
        return await rerank_chunks("query", chunks, top_n=20, keep=5)

    out = asyncio.run(run())
    assert len(out) == 5
    assert len(seen) == 1
    # Only the top 20 candidates (by original similarity) were cross-scored.
    assert [c["id"] for c in seen[0]] == [f"chunk-{i}" for i in range(20)]


def test_rerank_chunks_keep_clamped_to_top_n(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(rerank_mod, "_score_candidates", _fake_score_factory([0.9, 0.6, 0.3]))

    chunks = _chunks(5)

    async def run() -> Any:
        return await rerank_chunks("query", chunks, top_n=3, keep=10)

    out = asyncio.run(run())
    assert len(out) == 3  # clamped to top_n


def test_rerank_chunks_scores_all_when_top_n_nonpositive(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(
        rerank_mod,
        "_score_candidates",
        _fake_score_factory([float(7 - i) for i in range(7)]),
    )

    chunks = _chunks(7)

    async def run() -> Any:
        return await rerank_chunks("query", chunks, top_n=0, keep=0)

    out = asyncio.run(run())
    assert len(out) == 7


# ── 3. hybrid: RRF fusion of vector + keyword lanes ───────────────────


def test_hybrid_fuse_combines_vector_and_keyword_lanes() -> None:
    """RRF fusion merges per-collection vector lists + keyword hits by id."""
    v1 = [
        {"id": "d1", "similarity": 0.9},
        {"id": "d2", "similarity": 0.8},
    ]
    v2 = [
        {"id": "d3", "similarity": 0.85},
        {"id": "d1", "similarity": 0.7},
    ]
    kw = [
        {"id": "d4", "bm25_score": 1.2},  # keyword-only hit
        {"id": "d1", "bm25_score": 0.9},
    ]

    fused = _hybrid_fuse([v1, v2], kw)

    ids = [r["id"] for r in fused]
    # d1 appears in all three lanes -> top of the fused ranking.
    assert ids[0] == "d1"
    # Keyword-only hit surfaces alongside vector hits.
    assert "d4" in ids
    # Every fused entry carries an RRF score.
    assert all("rrf_score" in r for r in fused)
    # The vector entry (first seen) is preserved as the doc payload.
    d1 = next(r for r in fused if r["id"] == "d1")
    assert d1["similarity"] == 0.9


def test_hybrid_fuse_merges_duplicates_across_lanes() -> None:
    v1 = [{"id": "d1", "similarity": 0.9}, {"id": "d2", "similarity": 0.8}]
    kw = [{"id": "d1", "bm25_score": 5.0}]
    fused = _hybrid_fuse([v1], kw)
    assert len(fused) == 2  # d1 merged, not duplicated


# ── 4. hybrid: search_multi_collection wiring ─────────────────────────


async def _fake_search_similar(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    min_similarity: float = 0.0,
    content_limit: int = 500,
) -> Any:
    if collection == "coll_a":
        return [{"id": "va", "similarity": 0.9, "content": "vector a", "collection": "coll_a"}]
    if collection == "coll_b":
        return [{"id": "vb", "similarity": 0.85, "content": "vector b", "collection": "coll_b"}]
    return []


async def _fake_keyword_search(
    query: str,
    collections: list[str],
    limit: int = 20,
    max_docs_per_collection: int = 1000,
) -> Any:
    return [
        {
            "id": "kw1",
            "doc_id": "kw1",
            "bm25_score": 2.5,
            "collection": "coll_a",
            "metadata": {},
            "content": "keyword hit",
            "hybrid_source": "bm25",
        }
    ]


def test_search_multi_collection_hybrid_surfaces_keyword_only_hits(monkeypatch: pytest.MonkeyPatch) -> Any:
    """hybrid=True fuses the keyword lane; keyword-only hits survive truncation."""
    monkeypatch.setattr(engine_mod, "search_similar", _fake_search_similar)
    monkeypatch.setattr(engine_mod, "_hybrid_keyword_search", _fake_keyword_search)

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "some query", collections=["coll_a", "coll_b"], limit=3, hybrid=True
        )

    out = asyncio.run(run())
    ids = {r["id"] for r in out}
    assert "kw1" in ids  # keyword-only hit surfaced
    assert "va" in ids and "vb" in ids
    assert len(out) <= 3
    # Fused entries carry RRF attribution.
    assert any("rrf_score" in r for r in out)


def test_search_multi_collection_default_pure_vector(monkeypatch: pytest.MonkeyPatch) -> Any:
    """hybrid defaults OFF: keyword lane never runs, pure-vector behavior kept."""
    monkeypatch.setattr(engine_mod, "search_similar", _fake_search_similar)

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("keyword lane must not run when hybrid=False")

    monkeypatch.setattr(engine_mod, "_hybrid_keyword_search", _explode)

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "some query",
            collections=["coll_a", "coll_b"],
            limit=10,
            # rerank default is now True (see test_..._rerank_defaults_on); this
            # test isolates the hybrid-off behavior, so disable the reranker.
            rerank=False,
        )

    out = asyncio.run(run())
    assert [r["id"] for r in out] == ["va", "vb"]
    assert all("rrf_score" not in r for r in out)


# ── reranker: default ON + graceful timeout fallback ───────────────────


def test_search_multi_collection_rerank_defaults_on() -> None:
    """rerank defaults True on the main entry points (env-overridable)."""
    import inspect

    sig = inspect.signature(engine_mod.search_multi_collection)
    assert sig.parameters["rerank"].default is True
    sig3 = inspect.signature(engine_mod.three_pillar_search)
    assert sig3.parameters["rerank"].default is True


def test_search_multi_collection_rerank_default_applies(monkeypatch: pytest.MonkeyPatch) -> Any:
    """rerank=True default routes fused candidates through rerank_chunks."""
    monkeypatch.setattr(engine_mod, "search_similar", _fake_search_similar)

    calls: list[dict[str, Any]] = []

    async def fake_rerank_chunks(
        query: str,
        chunks: list[dict[str, Any]],
        top_n: int = 20,
        keep: int = 5,
    ) -> Any:
        calls.append({"top_n": top_n, "keep": keep, "count": len(chunks)})
        return [dict(c) for c in chunks][:keep]

    monkeypatch.setattr(rerank_mod, "rerank_chunks", fake_rerank_chunks)

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "some query", collections=["coll_a", "coll_b"], limit=2
        )

    asyncio.run(run())
    assert calls, "rerank_chunks must run by default (rerank=True)"
    assert calls[0] == {"top_n": 20, "keep": 2, "count": 2}


def test_search_multi_collection_rerank_timeout_falls_back(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Slow reranker (past timeout) → raw ranking kept, request never fails."""
    monkeypatch.setattr(engine_mod, "search_similar", _fake_search_similar)

    async def slow_rerank_chunks(
        query: str,
        chunks: list[dict[str, Any]],
        top_n: int = 20,
        keep: int = 5,
    ) -> list[dict[str, Any]]:
        await asyncio.sleep(0.5)
        return [dict(c) for c in chunks][:keep]

    monkeypatch.setattr(rerank_mod, "rerank_chunks", slow_rerank_chunks)

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "some query",
            collections=["coll_a", "coll_b"],
            limit=2,
            rerank=True,
            rerank_timeout=0.05,
        )

    out = asyncio.run(run())
    # Raw ranking preserved and surfaced as a no-op rerank (graceful).
    assert [r["id"] for r in out] == ["va", "vb"]
    assert all(r.get("rerank_method") == "none" for r in out)
    assert all(r.get("reranked") is False for r in out)


def test_three_pillar_search_mmr_lambda_knob_default() -> None:
    """MMR default lambda is 0.6 (relevance-leaning, not over-deduping)."""
    import inspect

    sig = inspect.signature(engine_mod.three_pillar_search)
    assert sig.parameters["use_mmr"].default is True
    assert sig.parameters["mmr_lambda"].default == 0.6
    # Knob surface: caller may pass a different lambda.
    assert "mmr_lambda" in sig.parameters


def test_search_multi_collection_rerank_trims_and_tags(monkeypatch: pytest.MonkeyPatch) -> Any:
    """rerank=True passes fused candidates to the search-lane reranker."""
    monkeypatch.setattr(engine_mod, "search_similar", _fake_search_similar)

    calls: list[dict[str, Any]] = []

    async def fake_rerank_chunks(
        query: str,
        chunks: list[dict[str, Any]],
        top_n: int = 20,
        keep: int = 5,
    ) -> Any:
        calls.append({"top_n": top_n, "keep": keep, "count": len(chunks)})
        out = []
        for i, c in enumerate(chunks):
            entry = dict(c)
            entry["rerank_score"] = float(len(chunks) - i)
            entry["reranked"] = True
            entry["rerank_method"] = "qwen3-reranker-8b"
            out.append(entry)
        return out[:keep]

    monkeypatch.setattr(rerank_mod, "rerank_chunks", fake_rerank_chunks)

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "some query",
            collections=["coll_a", "coll_b"],
            limit=1,
            rerank=True,
            rerank_top_k=20,
        )

    out = asyncio.run(run())
    assert len(out) == 1
    assert calls == [{"top_n": 20, "keep": 1, "count": 2}]
    assert all(r.get("reranked") for r in out)


# ── 5. hybrid: three_pillar_search wiring ─────────────────────────────


def _patch_three_pillar_externals(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch every external dependency three_pillar_search touches."""

    async def fake_get_embedder() -> Any:
        return _FakeEmbedder()

    async def fake_multi_collection(
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        min_similarity: float = 0.5,
        content_limit: int = 500,
        **kwargs: Any,
    ) -> Any:
        return [
            {"id": "va", "similarity": 0.9, "content": "vector a", "collection": "coll_a"},
            {"id": "vb", "similarity": 0.85, "content": "vector b", "collection": "coll_b"},
        ]

    async def fake_splade(
        query: str, collections: list[str] | None = None, limit: int = 20
    ) -> Any:
        return []

    monkeypatch.setattr(engine_mod, "get_embedder", fake_get_embedder)
    monkeypatch.setattr(engine_mod, "search_multi_collection", fake_multi_collection)
    monkeypatch.setattr("rugbrain.splade_bm25.splade_search", fake_splade)


def test_three_pillar_search_hybrid_fuses_keyword_lane(monkeypatch: pytest.MonkeyPatch) -> Any:
    _patch_three_pillar_externals(monkeypatch)
    monkeypatch.setattr(engine_mod, "_hybrid_keyword_search", _fake_keyword_search)

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "bitcoin price today",
            collections=["coll_a", "coll_b"],
            limit=10,
            hybrid=True,
            use_rating_boosts=False,
            use_parent_child=False,
            use_reranker=False,
        )

    out = asyncio.run(run())
    ids = {r["id"] for r in out["results"]}
    assert "kw1" in ids  # keyword-only hit fused in
    assert out["pillar_summary"]["hybrid_keyword_hits"] == 1
    assert "search_reranker_applied" in out["pillar_summary"]
    assert out["fusion_weights"]["sparse"] > 0


def test_three_pillar_search_hybrid_off_skips_keyword_lane(monkeypatch: pytest.MonkeyPatch) -> Any:
    _patch_three_pillar_externals(monkeypatch)

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("keyword lane must not run when hybrid=False")

    monkeypatch.setattr(engine_mod, "_hybrid_keyword_search", _explode)

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "bitcoin price today",
            collections=["coll_a", "coll_b"],
            limit=10,
            use_rating_boosts=False,
            use_parent_child=False,
            use_reranker=False,
        )

    out = asyncio.run(run())
    assert out["pillar_summary"]["hybrid_keyword_hits"] == 0
    assert {r["id"] for r in out["results"]} == {"va", "vb"}


def test_three_pillar_search_rerank_applies_before_truncation(monkeypatch: pytest.MonkeyPatch) -> Any:
    _patch_three_pillar_externals(monkeypatch)

    async def fake_rerank_chunks(
        query: str,
        chunks: list[dict[str, Any]],
        top_n: int = 20,
        keep: int = 5,
    ) -> Any:
        assert top_n == 20
        out = []
        for i, c in enumerate(chunks):
            entry = dict(c)
            entry["rerank_score"] = float(len(chunks) - i)
            entry["reranked"] = True
            entry["rerank_method"] = "qwen3-reranker-8b"
            out.append(entry)
        return out[:keep]

    monkeypatch.setattr(rerank_mod, "rerank_chunks", fake_rerank_chunks)

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "bitcoin price today",
            collections=["coll_a", "coll_b"],
            limit=1,
            rerank=True,
            rerank_top_k=20,
            use_rating_boosts=False,
            use_parent_child=False,
            use_reranker=False,
        )

    out = asyncio.run(run())
    assert len(out["results"]) == 1
    assert out["pillar_summary"]["search_reranker_applied"] is True
    assert out["pillar_summary"]["search_reranker_method"] == "qwen3-reranker-8b"
