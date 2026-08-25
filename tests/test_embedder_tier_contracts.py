"""Dual-tier embedder/reranker integration contracts — "know your place".

The box runs FOUR local models with strict job assignments:

  DEEP tier (long-form documents, corpus ingestion, big-corpus analysis)
    - qwen3-embedding-4B      :8004  native 2560-dim   (ALL stored vectors)
    - qwen3-reranker-4B       :8003  cross-encoder rerank
  SPEED tier (snappy answer pulls, near-dup, clustering, cache keys)
    - qwen3-embedding-0.6B    :8007  1024-dim          (BOTH sides internal only)
    - qwen3-reranker-0.6B     :8008  consumer rerank lane

Cloud fallback ladder (embeddings): local → gateway → OpenRouter nemotron
(2048-d, MRL-truncated) → NIM free tier → fail LOUD (T04: no hash fallback).
Rerank ladder: fast (:8008) → deep (:8003) → original order (fail-open).

These tests pin the PURPOSE ROUTING so a refactor can never silently swap
tiers: the fast embedder must never serve corpus search, the deep embedder
must never serve snappy near-dup hot loops, and every fallback rung fires
in the documented order.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from rugbrain.qwen_embedders import (
    OpenRouterEmbedder,
    Qwen3Embedder,
    Qwen3FastEmbedder,
)

# ── Tier identity contracts ─────────────────────────────────────────────────


def test_deep_tier_identity():
    """4B on :8004 at native 2560 — the ONLY tier allowed to write corpus vectors."""
    assert Qwen3Embedder.MODEL == "qwen3-embedding-4b"
    assert ":8004" in Qwen3Embedder.EMBED_URL
    assert Qwen3Embedder.DIMS == 2560


def test_speed_tier_identity():
    """0.6B on :8007 at 1024 — utility comparisons only, never corpus search."""
    assert Qwen3FastEmbedder.MODEL == "qwen3-embedding-0.6b"
    assert ":8007" in Qwen3FastEmbedder.EMBED_URL
    assert Qwen3FastEmbedder.DIMS == 1024


def test_cloud_fallback_identity():
    """OpenRouter nemotron is the cloud rung; its dim (2048) is MRL-truncatable."""
    assert "nemotron" in OpenRouterEmbedder.MODEL.lower()
    assert "openrouter.ai" in OpenRouterEmbedder.BASE


def test_fast_embedder_never_imported_by_search_paths():
    """Structural guard: corpus-search modules must not touch the 0.6B tier.

    A 0.6B query against a 4B-embedded collection is a cross-model space
    mismatch — Qdrant rejects it and search silently returns empty. The only
    legitimate 0.6B consumers are internal-similarity tools (near_dup).
    """
    search_modules = [
        "src/rugbrain/search/engine.py",
        "src/rugbrain/search/dual_tier.py",
        "src/rugbrain/routes/search_federated.py",
        "src/rugbrain/routes/core.py",
        "src/rugbrain/embeddings.py",
    ]
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in search_modules:
        p = root / rel
        if not p.exists():
            continue
        src = p.read_text()
        if "Qwen3FastEmbedder" in src or "QWEN_FAST_URL" in src:
            offenders.append(rel)
    assert not offenders, f"fast-tier embedder leaked into corpus-search paths: {offenders}"


def test_near_dup_is_the_fast_tier_consumer():
    """The 0.6B tier's documented consumer is near-duplicate detection."""
    near = (Path(__file__).resolve().parents[1] / "src/rugbrain/near_dup.py").read_text()
    assert "8007" in near or "QWEN_FAST_URL" in near or "0.6B" in near


# ── Rerank purpose routing ──────────────────────────────────────────────────


def test_rerank_lane_urls():
    """fast lane = 0.6B :8008, deep lane/fallback = 4B :8003."""
    from rugbrain.search import rerank as rr

    assert ":8008" in rr.RERANKER_URL_LIVE
    assert ":8003" in rr.RERANKER_URL


def test_federated_route_documents_lanes():
    """The federated API surface exposes exactly the three documented lanes."""
    src = (
        Path(__file__).resolve().parents[1] / "src/rugbrain/routes/search_federated.py"
    ).read_text()
    for token in ("fast", "deep", "off"):
        assert token in src, f"federated route lost the '{token}' lane"


@pytest.mark.asyncio
async def test_rerank_deep_tier_used_for_big_corpus(monkeypatch):
    """deep lane calls the 4B endpoint, never the consumer one."""
    from rugbrain.search import rerank as rr

    calls: list[str] = []

    async def fake_post(url, query, docs, timeout, label):  # noqa: ASYNC109
        calls.append(url)
        return [{"index": i, "relevance_score": 0.9} for i in range(len(docs))]

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    docs = [{"content": f"d{i}", "text": f"d{i}"} for i in range(5)]
    await rr._score_candidates("q", docs, tier="deep")
    assert calls and all(":8003" in u for u in calls), calls


@pytest.mark.asyncio
async def test_rerank_live_lane_ladder_fast_then_deep(monkeypatch):
    """live lane: 0.6B first; when it strains, ladder into the 4B — in order."""
    from rugbrain.search import rerank as rr

    calls: list[str] = []

    async def fake_post(url, query, docs, timeout, label):  # noqa: ASYNC109
        calls.append(url)
        if ":8008" in url:
            return None  # fast server strained
        return [{"index": i, "relevance_score": 0.9} for i in range(len(docs))]

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    docs = [{"content": f"d{i}", "text": f"d{i}"} for i in range(5)]
    scores, method = await rr._score_candidates("q", docs, tier="live")
    assert [":8008" in u for u in calls] == [True, False], calls
    assert method == "qwen3-reranker-4b"
    assert any(s > 0 for s in scores)


# ── Embed fallback ladder ORDER (auto mode) ─────────────────────────────────


@pytest.mark.asyncio
async def test_auto_ladder_order_int_gateway_or_nim(monkeypatch):
    """auto tries int → gateway → openrouter → nim, in that order, fail-loud."""
    from rugbrain import embeddings as emb

    order: list[str] = []

    async def _int(text, query=False, timeout=300.0):
        order.append("int")
        return None

    def _reachable():
        order.append("probe")
        return True

    async def _gw(text, query=False):
        order.append("gateway")
        return None

    async def _or(text):
        order.append("openrouter")
        return None

    async def _nim(text):
        order.append("nim")
        return None

    monkeypatch.setattr(emb, "_BACKEND", "auto")
    monkeypatch.setattr(emb, "_int_embed", _int)
    monkeypatch.setattr(emb, "_int_reachable", _reachable)
    monkeypatch.setattr(emb, "_gateway_embed", _gw)
    monkeypatch.setattr(emb, "_openrouter_embed", _or)
    monkeypatch.setattr(emb, "_nim_embed", _nim)

    with pytest.raises(emb.RAGUnavailableError):
        await emb._get_embedding_impl("ladder probe")

    assert order == ["probe", "int", "gateway", "openrouter", "nim"], order


@pytest.mark.asyncio
async def test_openrouter_mode_ladder_or_then_nim(monkeypatch):
    """Explicit openrouter mode: OR pool dry → NIM free tier → fail loud."""
    from rugbrain import embeddings as emb

    order: list[str] = []

    async def _or(text):
        order.append("openrouter")
        return None

    async def _nim(text):
        order.append("nim")
        return None

    monkeypatch.setattr(emb, "_BACKEND", "openrouter")
    monkeypatch.setattr(emb, "_openrouter_embed", _or)
    monkeypatch.setattr(emb, "_nim_embed", _nim)

    with pytest.raises(emb.RAGUnavailableError):
        await emb._get_embedding_impl("ladder probe")
    assert order == ["openrouter", "nim"], order


@pytest.mark.asyncio
async def test_int_mode_never_silently_degrades(monkeypatch):
    """T04 contract: explicit int backend failing raises instead of degrading."""
    from rugbrain import embeddings as emb

    async def _int(text, query=False, timeout=300.0):
        return None

    monkeypatch.setattr(emb, "_BACKEND", "int")
    monkeypatch.setattr(emb, "_int_embed", _int)

    with pytest.raises(emb.RAGUnavailableError):
        await emb._get_embedding_impl("no silent hash fallback")


# ── LIVE smoke: the four local models, right now (OPT-IN: TIER_SMOKE=1) ────

_LOCAL = os.getenv("TIER_SMOKE") == "1"  # opt-in: :8004 may be busy with ingest loads


@pytest.mark.skipif(not _LOCAL, reason="TIER_SMOKE=1 enables live-model smoke")
@pytest.mark.asyncio
async def test_live_four_local_models_alive_and_wired():
    """Round-trip each local model independently; report ALL failures at once.

    This is the 'are the four AIs actually wired to their jobs' check —
    run it after any llama-server restart, quota change, or model swap.
    NOTE: :8004 legitimately times out while migrate-embeds owns its slots;
    failures there during an active migration are informational, not regressions.
    """
    import httpx

    failures: list[str] = []

    async def probe(name: str, url: str, payload: dict, check) -> None:
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(url, json=payload)
            if r.status_code != 200:
                failures.append(f"{name}: HTTP {r.status_code} {r.text[:100]}")
                return
            check(r.json())
        except Exception as exc:  # noqa: BLE001 — diagnostic collects all
            failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:80]}")

    await probe(
        "deep-embed :8004",
        "http://172.22.0.1:8004/v1/embeddings",
        {"model": "qwen3-embedding-4b", "input": ["tier smoke"]},
        lambda d: (lambda n: n == 2560 or failures.append(f"deep-embed dim {n} != 2560"))(
            len(d["data"][0]["embedding"])
        ),
    )
    await probe(
        "speed-embed :8007",
        "http://172.22.0.1:8007/v1/embeddings",
        {"model": "qwen3-embedding-0.6b", "input": ["tier smoke"]},
        lambda d: (lambda n: n == 1024 or failures.append(f"speed-embed dim {n} != 1024"))(
            len(d["data"][0]["embedding"])
        ),
    )
    await probe(
        "deep-rerank :8003",
        "http://172.22.0.1:8003/v1/rerank",
        {
            "query": "rug pull",
            "documents": ["honeypot scam token", "defi yield protocol"],
        },
        lambda d: (
            (
                len(d["results"]) == 2
                and all(isinstance(s["relevance_score"], float) for s in d["results"])
            )
            or failures.append("deep-rerank bad result shape")
        ),
    )
    await probe(
        "fast-rerank :8008",
        "http://172.22.0.1:8008/v1/rerank",
        {
            "query": "rug pull",
            "documents": ["honeypot scam token", "defi yield protocol"],
        },
        lambda d: len(d["results"]) == 2 or failures.append("fast-rerank bad shape"),
    )

    assert not failures, "local-tier smoke failures:\n" + "\n".join(failures)
