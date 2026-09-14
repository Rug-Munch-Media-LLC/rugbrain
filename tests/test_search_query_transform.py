"""Targeted tests for the retrieval-layer upgrades.

Covers the four audit-flagged techniques added to the search lane:

  * HyDE + multi-query expansion (``rugbrain.search.query_transform``) —
    ``hyde_expand`` / ``multi_query_expand`` and their wiring into
    ``search_multi_collection`` / ``three_pillar_search`` (additive,
    default OFF, no-op fallback without a live LLM);
  * parent-child at ingest (``ingest_document_parent_child``) plus the
    retrieval-side connect (engine parent-child expansion reads parents
    stored by the ingest seam);
  * reranker on by default (``three_pillar_search.use_reranker=True`` with
    the graceful no-op fallback);
  * recency-aware fusion (``rugbrain.search.fusion``) — half-life decay on
    ``published``/date metadata folded into the RRF weight (default OFF).

All unit tests: no external services, no ML models required.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import rugbrain.parent_retriever as parent_retriever_mod
import rugbrain.search.engine as engine_mod
import rugbrain.search.filters as filters_mod
from rugbrain.search.fusion import (
    apply_recency_decay,
    extract_doc_date,
    half_life_decay,
    rrf_fuse_recency,
)
from rugbrain.search.query_transform import expand_synonyms, hyde_expand, multi_query_expand

# ── shared fakes ──────────────────────────────────────────────────────


class _FakeRedis:
    """Minimal in-memory async Redis stand-in (get/set/setex/sadd + pipeline)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._ttl: dict[str, int] = {}
        self._sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value
        self._ttl[key] = ttl

    async def sadd(self, key: str, member: str) -> None:
        self._sets.setdefault(key, set()).add(member)

    async def smembers(self, key: str) -> list[str]:
        return list(self._sets.get(key, set()))

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._keys: list[str] = []

    def get(self, key: str) -> _FakePipeline:
        self._keys.append(key)
        return self

    async def execute(self) -> list[str | None]:
        return [self._redis._data.get(k) for k in self._keys]


class _RecordingEmbedder:
    """Records every embedded text; returns a fixed vector."""

    def __init__(self) -> None:
        self.embedded: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.embedded.append(text)
        return [0.1, 0.2, 0.3]

    async def _semantic_embed_one(self, text: str, head: str) -> list[float]:
        self.embedded.append(text)
        return [0.1, 0.2, 0.3]

    async def search(
        self, query: str, coll: str, limit: int = 10, min_similarity: float = 0.0
    ) -> list[dict[str, Any]]:
        return []


def _async_get_embedder(embedder: _RecordingEmbedder) -> Any:
    async def fake_get_embedder() -> _RecordingEmbedder:
        return embedder

    return fake_get_embedder


def _async_get_redis(redis: _FakeRedis) -> Any:
    async def fake_get_redis() -> _FakeRedis:
        return redis

    return fake_get_redis


# ── 1. query_transform: multi-query expansion ─────────────────────────


def test_multi_query_expand_noop_without_llm() -> None:
    assert asyncio.run(multi_query_expand("bitcoin etf approval news")) == [
        "bitcoin etf approval news"
    ]


def test_multi_query_expand_with_sync_llm() -> Any:
    def llm(query: str) -> list[str]:
        return [
            "bitcoin ETF approval regulatory news",
            "BTC exchange-traded fund SEC decision",
            "bitcoin spot ETF market impact",
        ]

    out = asyncio.run(multi_query_expand("bitcoin etf approval news", llm_fn=llm))
    assert out[0] == "bitcoin etf approval news"
    assert len(out) == 4
    assert "bitcoin ETF approval regulatory news" in out


def test_multi_query_expand_with_async_llm() -> Any:
    async def llm(query: str) -> list[str]:
        return ["async rewrite one", "async rewrite two"]

    out = asyncio.run(multi_query_expand("some query", llm_fn=llm))
    assert out == ["some query", "async rewrite one", "async rewrite two"]


def test_multi_query_expand_caps_at_four_variants() -> Any:
    def llm(query: str) -> list[str]:
        return [f"rewrite {i}" for i in range(10)]

    out = asyncio.run(multi_query_expand("q", llm_fn=llm))
    assert len(out) == 4  # original + 3 rewrites


def test_multi_query_expand_dedupes_and_drops_non_strings() -> Any:
    def llm(query: str) -> Any:
        return ["q", "same as original", 123, "same as original", "unique"]

    out = asyncio.run(multi_query_expand("q", llm_fn=llm))
    assert out == ["q", "same as original", "unique"]


def test_multi_query_expand_failure_falls_back_to_noop() -> None:
    def llm(query: str) -> list[str]:
        raise RuntimeError("model down")

    assert asyncio.run(multi_query_expand("q", llm_fn=llm)) == ["q"]


def test_multi_query_expand_accepts_comma_separated_string() -> Any:
    def llm(query: str) -> Any:
        return "alpha, beta; gamma"

    out = asyncio.run(multi_query_expand("q", llm_fn=llm))
    assert out == ["q", "alpha", "beta", "gamma"]


# ── 2. query_transform: HyDE ──────────────────────────────────────────


def test_expand_synonyms_maps_crypto_terms() -> None:
    """Common crypto terms gain alias variants (rug pull→rugpull/honeypot)."""
    out = expand_synonyms("rug pull token cannot sell")
    assert out[0] == "rug pull token cannot sell"
    assert any("rugpull" in v for v in out)
    assert any("honeypot" in v for v in out)


def test_expand_synonyms_flash_loan_and_sandwich() -> None:
    assert any("flashloan" in v for v in expand_synonyms("flash loan attack how it works"))
    mev = expand_synonyms("MEV sandwich attack prevention")
    assert any("frontrunning" in v for v in mev)


def test_expand_synonyms_oracle_manipulation() -> None:
    out = expand_synonyms("oracle manipulation TWAP spot price attack")
    assert any("price manipulation" in v for v in out)


def test_expand_synonyms_noop_unmatched() -> None:
    assert expand_synonyms("bitcoin price today") == ["bitcoin price today"]


def test_expand_synonyms_bounded() -> None:
    q = "rug pull flash loan sandwich oracle manipulation"
    out = expand_synonyms(q)
    assert out[0] == q
    assert 1 <= len(out) <= 4


def test_search_multi_collection_synonym_expand_searches_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """synonym_expand=True searches the original query plus alias variants."""
    searches: list[str] = []

    async def fake_search_similar(
        query: str,
        collection: str = "wallet_profiles",
        limit: int = 10,
        min_similarity: float = 0.0,
        content_limit: int = 500,
    ) -> Any:
        searches.append(query)
        return [
            {
                "id": f"{collection}-{len(searches)}",
                "similarity": 0.9,
                "content": query,
                "collection": collection,
            }
        ]

    monkeypatch.setattr(engine_mod, "search_similar", fake_search_similar)

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "rug pull token cannot sell",
            collections=["coll_a"],
            limit=2,
            synonym_expand=True,
            rerank=False,
        )

    out = asyncio.run(run())
    assert searches[0] == "rug pull token cannot sell"
    assert any("honeypot" in q for q in searches)
    assert len(searches) >= 2
    assert out


def test_hyde_expand_noop_fallback_uses_query() -> Any:
    embedded: list[str] = []

    async def embed_fn(text: str) -> list[float]:
        embedded.append(text)
        return [1.0, 2.0]

    out = asyncio.run(hyde_expand("reentrancy guard solidity", embed_fn=embed_fn))
    assert out["hypothetical_doc"] == "reentrancy guard solidity"
    assert out["embedding"] == [1.0, 2.0]
    assert out["used_llm"] is False
    assert embedded == ["reentrancy guard solidity"]


def test_hyde_expand_with_llm_embeds_hypothetical_doc() -> Any:
    embedded: list[str] = []

    async def embed_fn(text: str) -> list[float]:
        embedded.append(text)
        return [1.0, 2.0]

    def llm(query: str) -> str:
        return "A reentrancy guard prevents reentrancy via a nonReentrant modifier and CEI."

    out = asyncio.run(hyde_expand("reentrancy guard solidity", embed_fn=embed_fn, llm_fn=llm))
    assert out["used_llm"] is True
    assert "nonReentrant" in out["hypothetical_doc"]
    assert embedded == [out["hypothetical_doc"]]


def test_hyde_expand_llm_failure_falls_back() -> Any:
    def llm(query: str) -> str:
        raise RuntimeError("down")

    async def embed_fn(text: str) -> list[float]:
        return [0.5]

    out = asyncio.run(hyde_expand("q", embed_fn=embed_fn, llm_fn=llm))
    assert out["used_llm"] is False
    assert out["hypothetical_doc"] == "q"


def test_hyde_expand_supports_sync_embed_fn() -> Any:
    def embed_fn(text: str) -> list[float]:
        return [len(text)]

    out = asyncio.run(hyde_expand("q", embed_fn=embed_fn))
    assert out["embedding"] == [1]


# ── 3. fusion: recency-aware decay helpers ────────────────────────────


def test_half_life_decay_math() -> None:
    assert half_life_decay(0.0, 30.0) == pytest.approx(1.0)
    assert half_life_decay(30.0, 30.0) == pytest.approx(0.5)
    assert half_life_decay(60.0, 30.0) == pytest.approx(0.25)
    assert half_life_decay(10.0, 0.0) == 1.0  # disabled when half-life <= 0


def test_extract_doc_date_parses_iso_epoch_and_missing() -> None:
    now = datetime.now(UTC)
    iso = now.isoformat()
    assert extract_doc_date({"metadata": {"published": iso}}) is not None
    assert extract_doc_date({"metadata": {"published_at": now.timestamp()}}) is not None
    assert extract_doc_date({"metadata": {"date": now.timestamp() * 1000}}) is not None
    assert extract_doc_date({"published": iso}) is not None  # top-level fallback
    assert extract_doc_date({"metadata": {}}) is None
    assert extract_doc_date({}) is None
    assert extract_doc_date({"metadata": {"published": "not-a-date"}}) is None


def test_apply_recency_decay_orders_newer_first_and_tags() -> None:
    old = datetime.now(UTC) - timedelta(days=400)
    fresh = datetime.now(UTC) - timedelta(days=1)
    results = [
        {"id": "old", "similarity": 0.9, "metadata": {"published": old.isoformat()}},
        {"id": "fresh", "similarity": 0.8, "metadata": {"published": fresh.isoformat()}},
    ]
    out = apply_recency_decay(results, half_life_days=30.0)
    assert [r["id"] for r in out] == ["fresh", "old"]
    # fresh doc aged ~1 day → decay ≈ 0.5 ** (1/30)
    assert out[0]["recency_decay"] == pytest.approx(0.5 ** (1 / 30), abs=0.01)
    assert out[1]["recency_decay"] < 0.01
    # Decay was folded into the retrieval score: the fresher doc wins even
    # though its raw similarity was lower.
    assert out[0]["similarity"] > out[1]["similarity"]


def test_apply_recency_decay_leaves_docs_without_date_unchanged() -> None:
    results = [
        {"id": "a", "rrf_score": 0.5, "metadata": {}},
        {"id": "b", "rrf_score": 0.4, "metadata": {"published": "2020-01-01T00:00:00Z"}},
    ]
    out = apply_recency_decay(results, half_life_days=30.0)
    by_id = {r["id"]: r for r in out}
    assert by_id["a"]["rrf_score"] == 0.5  # untouched
    assert by_id["a"]["recency_decay"] == 1.0
    assert by_id["b"]["rrf_score"] < 0.4


def test_rrf_fuse_recency_equals_rrf_when_disabled() -> None:
    lists = [
        [{"id": "a", "similarity": 0.9}, {"id": "b", "similarity": 0.8}],
        [{"id": "b", "similarity": 0.7}, {"id": "c", "similarity": 0.6}],
    ]
    out = rrf_fuse_recency(lists, half_life_days=0.0)
    assert {r["id"] for r in out} == {"a", "b", "c"}
    assert all(r.get("recency_decay") is None for r in out)


def test_rrf_fuse_recency_applies_half_life_decay() -> None:
    old = datetime.now(UTC) - timedelta(days=300)
    fresh = datetime.now(UTC) - timedelta(days=1)
    lists = [
        [
            {"id": "old", "similarity": 0.9, "metadata": {"published": old.isoformat()}},
            {"id": "fresh", "similarity": 0.8, "metadata": {"published": fresh.isoformat()}},
        ]
    ]
    out = rrf_fuse_recency(lists, half_life_days=30.0)
    assert [r["id"] for r in out] == ["fresh", "old"]
    assert all(r.get("recency_decay") is not None for r in out)


# ── 4. engine wiring: search_multi_collection expansion ───────────────


def test_search_multi_collection_default_no_expansion(monkeypatch: pytest.MonkeyPatch) -> Any:
    """hyde/multi_query default OFF: no extra embedder calls, single search."""
    embedder = _RecordingEmbedder()
    monkeypatch.setattr(engine_mod, "get_embedder", _async_get_embedder(embedder))

    async def fake_search_similar(
        query: str,
        collection: str = "wallet_profiles",
        limit: int = 10,
        min_similarity: float = 0.0,
        content_limit: int = 500,
    ) -> Any:
        return [
            {"id": f"{collection}-x", "similarity": 0.9, "content": query, "collection": collection}
        ]

    monkeypatch.setattr(engine_mod, "search_similar", fake_search_similar)

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "bitcoin price", collections=["coll_a"], limit=2
        )

    out = asyncio.run(run())
    assert len(out) == 1
    assert embedder.embedded == []  # expansion fully off — embedder never touched


def test_search_multi_collection_hyde_expands_query(monkeypatch: pytest.MonkeyPatch) -> Any:
    """hyde=True searches the original query AND the hypothetical doc."""
    embedder = _RecordingEmbedder()
    monkeypatch.setattr(engine_mod, "get_embedder", _async_get_embedder(embedder))
    searches: list[str] = []

    async def fake_search_similar(
        query: str,
        collection: str = "wallet_profiles",
        limit: int = 10,
        min_similarity: float = 0.0,
        content_limit: int = 500,
    ) -> Any:
        searches.append(query)
        return [
            {
                "id": f"{collection}-{len(searches)}",
                "similarity": 0.9,
                "content": query,
                "collection": collection,
            }
        ]

    monkeypatch.setattr(engine_mod, "search_similar", fake_search_similar)

    def llm(query: str) -> str:
        return "Hypothetical answer: the reentrancy guard uses checks-effects-interactions."

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "reentrancy guard solidity",
            collections=["coll_a"],
            limit=4,
            hyde=True,
            llm_fn=llm,
        )

    out = asyncio.run(run())
    assert "reentrancy guard solidity" in searches
    assert any("Hypothetical" in q for q in searches)
    assert len(searches) == 2
    # HyDE hypothetical was embedded (once) for the variant search.
    assert any("Hypothetical" in t for t in embedder.embedded)
    assert out


def test_search_multi_collection_multi_query_expands(monkeypatch: pytest.MonkeyPatch) -> Any:
    """multi_query=True searches every variant; results are RRF-fused."""
    embedder = _RecordingEmbedder()
    monkeypatch.setattr(engine_mod, "get_embedder", _async_get_embedder(embedder))
    searches: list[str] = []

    async def fake_search_similar(
        query: str,
        collection: str = "wallet_profiles",
        limit: int = 10,
        min_similarity: float = 0.0,
        content_limit: int = 500,
    ) -> Any:
        searches.append(query)
        return [
            {
                "id": f"{collection}-{len(searches)}",
                "similarity": 0.9,
                "content": query,
                "collection": collection,
            }
        ]

    monkeypatch.setattr(engine_mod, "search_similar", fake_search_similar)

    def llm(query: str) -> list[str]:
        return [
            "how to secure solidity against reentrancy",
            "nonReentrant modifier example",
            "CEI pattern solidity",
        ]

    async def run() -> Any:
        return await engine_mod.search_multi_collection(
            "reentrancy guard solidity",
            collections=["coll_a"],
            limit=4,
            multi_query=True,
            llm_fn=llm,
        )

    out = asyncio.run(run())
    assert len(searches) == 4  # original + 3 rewrites
    assert searches[0] == "reentrancy guard solidity"
    assert "CEI pattern solidity" in searches
    assert len(out) == 4  # fused results from all variants, truncated at limit


# ── 5. engine wiring: three_pillar_search ─────────────────────────────


def test_three_pillar_reranker_defaults_on() -> None:
    sig = inspect.signature(engine_mod.three_pillar_search)
    assert sig.parameters["use_reranker"].default is True


def test_three_pillar_search_defaults_are_additive() -> None:
    """The new params exist and are OFF by default (except use_reranker)."""
    sig = inspect.signature(engine_mod.three_pillar_search)
    assert sig.parameters["hyde"].default is False
    assert sig.parameters["multi_query"].default is False
    assert sig.parameters["llm_fn"].default is None
    assert sig.parameters["use_recency_decay"].default is False
    assert sig.parameters["recency_half_life_days"].default == 30.0
    assert sig.parameters["recency_date_field"].default == "published"
    sig2 = inspect.signature(engine_mod.search_multi_collection)
    assert sig2.parameters["hyde"].default is False
    assert sig2.parameters["multi_query"].default is False


def _patch_three_pillar_externals(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch every external dependency three_pillar_search touches.

    Returns a recorder dict so callers can assert on kwargs passed to the
    mocked ``search_multi_collection`` fallback.
    """
    recorder: dict[str, Any] = {"search_multi_collection_calls": []}

    async def fake_get_embedder() -> Any:
        return _RecordingEmbedder()

    async def fake_multi_collection(
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        min_similarity: float = 0.5,
        content_limit: int = 500,
        **kwargs: Any,
    ) -> Any:
        recorder["search_multi_collection_calls"].append(kwargs)
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
    return recorder


def test_three_pillar_search_expansion_passthrough(monkeypatch: pytest.MonkeyPatch) -> Any:
    """hyde/multi_query/llm_fn reach the dense fallback when ANN is absent."""
    recorder = _patch_three_pillar_externals(monkeypatch)

    def llm(query: str) -> str:
        return "Hypothetical doc about the attack vector"

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "what is a flash loan attack pattern",
            collections=["coll_a", "coll_b"],
            limit=5,
            use_reranker=False,
            use_rating_boosts=False,
            use_parent_child=False,
            hyde=True,
            multi_query=True,
            llm_fn=llm,
        )

    out = asyncio.run(run())
    assert out["pillar_summary"]["query_expansion"]["hyde"] is True
    assert out["pillar_summary"]["query_expansion"]["multi_query"] is True
    assert out["pillar_summary"]["query_expansion"]["variants"] >= 2
    calls = recorder["search_multi_collection_calls"]
    assert calls, "dense fallback should have run"
    assert calls[0]["hyde"] is True
    assert calls[0]["multi_query"] is True
    assert calls[0]["llm_fn"] is llm


def test_three_pillar_search_recency_fusion(monkeypatch: pytest.MonkeyPatch) -> Any:
    """use_recency_decay=True folds half-life decay into the RRF weight."""
    _patch_three_pillar_externals(monkeypatch)
    old = (datetime.now(UTC) - timedelta(days=500)).isoformat()
    fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    async def fake_multi_collection(
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        min_similarity: float = 0.5,
        content_limit: int = 500,
        **kwargs: Any,
    ) -> Any:
        return [
            {
                "id": "fresh",
                "similarity": 0.8,
                "content": "fresh",
                "collection": "coll_a",
                "metadata": {"published": fresh},
            },
            {
                "id": "old",
                "similarity": 0.9,
                "content": "old",
                "collection": "coll_a",
                "metadata": {"published": old},
            },
        ]

    monkeypatch.setattr(engine_mod, "search_multi_collection", fake_multi_collection)

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "bitcoin price today",
            collections=["coll_a"],
            limit=2,
            rerank=False,
            use_reranker=False,
            use_rating_boosts=False,
            use_parent_child=False,
            use_recency_decay=True,
            recency_half_life_days=30.0,
        )

    out = asyncio.run(run())
    results = out["results"]
    assert results and results[0]["id"] == "fresh"
    assert out["pillar_summary"]["recency_applied"] is True
    assert out["pillar_summary"]["recency_half_life_days"] == 30.0
    assert all("recency_decay" in r for r in results)


def test_three_pillar_search_recency_off_by_default(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Default OFF: no recency decay, no summary tag."""
    _patch_three_pillar_externals(monkeypatch)

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "bitcoin price today",
            collections=["coll_a", "coll_b"],
            limit=2,
            use_reranker=False,
            use_rating_boosts=False,
            use_parent_child=False,
        )

    out = asyncio.run(run())
    assert out["pillar_summary"]["recency_applied"] is False
    assert all("recency_decay" not in r for r in out["results"])


def test_three_pillar_search_hyde_dense_loop_uses_variants(monkeypatch: pytest.MonkeyPatch) -> Any:
    """With ANN available, hyde runs one dense search per variant.

    The Qdrant-backed ``search_multi_collection`` is the primary pillar-1
    path; this test forces it to fail so the standalone ANN fallback (which
    runs one search per HyDE variant) is exercised.
    """

    async def _raise_multi(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("qdrant unavailable (test)")

    monkeypatch.setattr(engine_mod, "search_multi_collection", _raise_multi)

    class _FakeCache:
        async def check(self, vec: list[float]) -> None:
            return None

        async def store(self, vec: list[float], results: list[dict[str, Any]]) -> None:
            pass

    monkeypatch.setattr("rugbrain.compat.get_semantic_cache", lambda: _FakeCache())

    class _FakeAnn:
        def __init__(self) -> None:
            self.searches: list[list[float]] = []

        def is_built(self, collection: str) -> bool:
            return True

        def bump_version(self, collection: str) -> None:
            pass

        async def search(
            self, query_embedding: list[float], collection: str, limit: int, min_similarity: float
        ) -> list[dict[str, Any]]:
            self.searches.append(query_embedding)
            return [
                {"id": f"{collection}-r1", "similarity": 0.9},
                {"id": f"{collection}-r2", "similarity": 0.8},
            ]

    fake_ann = _FakeAnn()
    monkeypatch.setattr("rugbrain.compat.get_ann_index", lambda: fake_ann)

    fake_redis = _FakeRedis()
    for cid in ("coll_a-r1", "coll_a-r2"):
        fake_redis._data[f"rag:coll_a:{cid}"] = json.dumps(
            {
                "id": cid,
                "content": f"content {cid}",
                "metadata": {"section": "s"},
                "collection": "coll_a",
            }
        )
    monkeypatch.setattr(engine_mod, "_get_redis", _async_get_redis(fake_redis))

    embedder = _RecordingEmbedder()
    monkeypatch.setattr(engine_mod, "get_embedder", _async_get_embedder(embedder))

    async def fake_splade(
        query: str, collections: list[str] | None = None, limit: int = 20
    ) -> Any:
        return []

    monkeypatch.setattr("rugbrain.splade_bm25.splade_search", fake_splade)

    def llm(query: str) -> str:
        return "Hypothetical: an attacker borrows flash loan liquidity and drains the pool."

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "flash loan attack pattern",
            collections=["coll_a"],
            limit=2,
            use_mmr=False,
            use_kg=False,
            use_reranker=False,
            use_rating_boosts=False,
            use_parent_child=False,
            hyde=True,
            llm_fn=llm,
        )

    out = asyncio.run(run())
    # Original query + hypothetical doc embedded; one ANN search per variant.
    assert any("Hypothetical" in t for t in embedder.embedded)
    assert len(fake_ann.searches) == 2
    assert out["pillar_summary"]["query_expansion"]["hyde"] is True
    assert out["pillar_summary"]["query_expansion"]["variants"] == 2
    assert out["results"]


# ── 6. parent-child: ingest seam + retrieval connect ──────────────────


def test_ingest_document_parent_child_splits_and_links(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake_redis = _FakeRedis()
    monkeypatch.setattr(filters_mod, "_get_redis", _async_get_redis(fake_redis))
    embedder = _RecordingEmbedder()
    monkeypatch.setattr(filters_mod, "get_embedder", _async_get_embedder(embedder))

    fake_pr = parent_retriever_mod.ParentRetriever(redis="unused")
    stored: list[dict[str, Any]] = []
    fake_pr.store_parent = _record_store_parent(stored)

    async def fake_get_parent_retriever() -> Any:
        return fake_pr

    monkeypatch.setattr(parent_retriever_mod, "get_parent_retriever", fake_get_parent_retriever)

    doc = (
        "# Audit Report\n\n"
        + ("The contract at 0xabc shows honeypot signals and locked liquidity. " * 15)
        + "\n\n# Findings\n\n"
        + ("Reentrancy risk detected in the withdraw function. " * 15)
    )

    async def run() -> Any:
        return await filters_mod.ingest_document(
            "news_articles", doc, metadata={"source": "auditor"}, parent_child=True
        )

    out = asyncio.run(run())
    assert out["parent_child"] is True
    assert out["parent_count"] >= 2  # one parent window per section
    assert out["child_count"] >= 2
    assert out["child_ids"]

    # Parent windows stored under the chunk-keyed layout the engine reads.
    child_meta_parents: set[str] = set()
    for cid in out["child_ids"]:
        raw = fake_redis._data[f"rag:news_articles:{cid}"]
        doc_json = json.loads(raw)
        pid = doc_json["metadata"]["parent_id"]
        assert pid
        child_meta_parents.add(pid)
        # Parent window exists for every referenced parent_id.
        assert f"rag:news_articles:{pid}" in fake_redis._data
        parent_json = json.loads(fake_redis._data[f"rag:news_articles:{pid}"])
        assert parent_json["metadata"]["parent_id"] == pid
        assert parent_json["metadata"].get("is_parent") is True
        assert "Audit Report" in parent_json["content"] or "Findings" in parent_json["content"]

    # Parent-retriever layout also populated via store_parent.
    assert stored, "parent retriever store should have been called"
    assert {s["doc_id"] for s in stored} == child_meta_parents

    # Children are in the searchable index; parents are NOT.
    indexed = set(fake_redis._sets.get("rag:idx:news_articles", set()))
    assert indexed == set(out["child_ids"])
    assert not (indexed & child_meta_parents)


def test_ingest_document_parent_child_falls_back_on_unsplittable(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake_redis = _FakeRedis()
    monkeypatch.setattr(filters_mod, "_get_redis", _async_get_redis(fake_redis))
    embedder = _RecordingEmbedder()
    monkeypatch.setattr(filters_mod, "get_embedder", _async_get_embedder(embedder))

    async def run() -> Any:
        return await filters_mod.ingest_document("news_articles", "   \n  ", parent_child=True)

    out = asyncio.run(run())
    assert out["parent_child"] is True
    assert out["parent_count"] == 0
    assert out["child_count"] == 1


def _record_store_parent(stored: list[dict[str, Any]]) -> Any:
    async def store_parent(doc_id: str, content: str, metadata: dict[str, Any]) -> None:
        stored.append({"doc_id": doc_id, "content": content, "metadata": metadata})

    return store_parent


def test_fetch_parent_context_chunk_keyed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    fake_redis._data["rag:docs:p123"] = json.dumps(
        {"content": "parent text", "metadata": {"title": "P"}}
    )
    monkeypatch.setattr(engine_mod, "_get_redis", _async_get_redis(fake_redis))

    out = asyncio.run(engine_mod._fetch_parent_context("docs", "p123"))
    assert out is not None
    assert out["content"] == "parent text"
    assert out["metadata"]["title"] == "P"


def test_fetch_parent_context_parent_retriever_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_redis = _FakeRedis()
    fake_redis._data["rag:parent:p456"] = json.dumps(
        {"doc_id": "p456", "content": "parent via retriever", "metadata": {}}
    )
    monkeypatch.setattr(engine_mod, "_get_redis", _async_get_redis(fake_redis))

    out = asyncio.run(engine_mod._fetch_parent_context("docs", "p456"))
    assert out is not None
    assert out["content"] == "parent via retriever"


def test_fetch_parent_context_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_mod, "_get_redis", _async_get_redis(_FakeRedis()))
    assert asyncio.run(engine_mod._fetch_parent_context("docs", "missing")) is None


def test_three_pillar_search_expands_child_to_parent(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A matched child with parent_id gains parent_content in retrieval."""
    fake_redis = _FakeRedis()
    fake_redis._data["rag:coll_a:p-parent"] = json.dumps(
        {
            "content": "Full parent context: the withdraw function lacks a reentrancy guard.",
            "metadata": {"parent_id": "p-parent", "is_parent": True},
        }
    )
    monkeypatch.setattr(engine_mod, "_get_redis", _async_get_redis(fake_redis))

    async def fake_get_embedder() -> Any:
        return _RecordingEmbedder()

    async def fake_multi_collection(
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        min_similarity: float = 0.5,
        content_limit: int = 500,
        **kwargs: Any,
    ) -> Any:
        return [
            {
                "id": "child-1",
                "similarity": 0.9,
                "content": "short child chunk",
                "collection": "coll_a",
                "metadata": {"parent_id": "p-parent"},
            }
        ]

    async def fake_splade(
        query: str, collections: list[str] | None = None, limit: int = 20
    ) -> Any:
        return []

    monkeypatch.setattr(engine_mod, "get_embedder", fake_get_embedder)
    monkeypatch.setattr(engine_mod, "search_multi_collection", fake_multi_collection)
    monkeypatch.setattr("rugbrain.splade_bm25.splade_search", fake_splade)

    async def run() -> Any:
        return await engine_mod.three_pillar_search(
            "reentrancy guard withdraw",
            collections=["coll_a"],
            limit=1,
            use_reranker=False,
            use_rating_boosts=False,
            use_parent_child=True,
        )

    out = asyncio.run(run())
    assert out["results"]
    result = out["results"][0]
    assert result["parent_id"] == "p-parent"
    assert "Full parent context" in result["parent_content"]
    assert out["pillar_summary"]["parent_child_applied"] is True
