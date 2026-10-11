"""Dual-tier embedder/reranker integration contracts — "know your place".

The box runs FOUR local models with strict job assignments:

  DEEP tier (long-form documents, corpus ingestion, big-corpus analysis)
    - qwen3-embedding-4B      :8004  native 2560-dim   (ALL stored vectors)
    - qwen3-reranker-4B       :8003  cross-encoder rerank
  SPEED tier (snappy answer pulls, near-dup, clustering, cache keys)
    - qwen3-embedding-8b    :8007  1024-dim          (BOTH sides internal only)
    - qwen3-reranker-0.6B     :8008  consumer rerank lane

Cloud fallback ladder (embeddings, 2026-08-29 consolidation): DeepInfra
(same qwen3-embedding-8b model ⇒ 2560-d index-compatible) → local :8004 →
fail LOUD (T04: no hash fallback). Gateway / OpenRouter / NIM are legacy
lanes, explicit RAG_EMBED_BACKEND overrides only.
Rerank ladder: fast (:8008) → deep (:8003) → original order (fail-open).

These tests pin the PURPOSE ROUTING so a refactor can never silently swap
tiers: the fast embedder must never serve corpus search, the deep embedder
must never serve snappy near-dup hot loops, and every fallback rung fires
in the documented order.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rugbrain.qwen_embedders import (
    OpenRouterEmbedder,
    Qwen3Embedder,
)

# ── Tier identity contracts ─────────────────────────────────────────────────


def test_deep_tier_identity():
    """8B on :8004 at native 4096 — the ONLY tier allowed to write corpus vectors.

    The 4B/2560 generation is eliminated (operator decision 2026-09-29). This
    contract previously asserted 4B/2560, which pinned the retired lane as the
    embedder's identity: any code that restored it passed.
    """
    assert Qwen3Embedder.MODEL == "qwen3-embedding-8b"
    assert ":8004" in Qwen3Embedder.EMBED_URL
    assert Qwen3Embedder.DIMS == 4096


def test_deep_tier_never_defaults_to_retired_4b():
    """No tier identity in the embedder facade may name the retired generation."""
    from rugbrain.embedder_providers.qwen3 import DEEPINFRA_EMBED_MODEL

    for name in (Qwen3Embedder.MODEL, DEEPINFRA_EMBED_MODEL):
        assert "4b" not in name.lower(), f"retired 4B model default reachable: {name}"


def test_speed_tier_is_retired():
    """The 0.6B tier is gone: no class, no endpoint references anywhere."""
    import rugbrain.qwen_embedders as qe

    assert not hasattr(qe, "Qwen3FastEmbedder")
    src = (Path(__file__).resolve().parents[1] / "src/rugbrain/qwen_embedders.py").read_text()
    assert ":8007" not in src


def test_cloud_fallback_identity():
    """OpenRouter nemotron is the cloud rung; its dim (2048) is MRL-truncatable."""
    assert "nemotron" in OpenRouterEmbedder.MODEL.lower()
    assert "openrouter.ai" in OpenRouterEmbedder.BASE


def test_fast_embedder_never_imported_by_search_paths():
    """Structural guard: corpus-search modules must not touch the 0.6B tier.

    A 0.6B query against a 4B-embedded collection is a cross-model space
    mismatch — Qdrant rejects it and search silently returns empty. The only
    legitimate 0.6B consumers are internal-similarity tools (near_dup) and,
    since 2026-08-29, the LEGACY speed helpers in embeddings.py
    (get_embedding_fast / get_query_embedding_speed — legacy reads only;
    retirement of those callers tracked separately). New WRITE paths must
    never touch the 0.6B embed tier.
    """
    search_modules = [
        "src/rugbrain/search/engine.py",
        "src/rugbrain/search/dual_tier.py",
        "src/rugbrain/routes/search_federated.py",
        "src/rugbrain/routes/core.py",
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


def test_near_dup_no_longer_pins_the_retired_tier():
    """near_dup takes an injected embedder; the 0.6B pin is gone."""
    near = (Path(__file__).resolve().parents[1] / "src/rugbrain/near_dup.py").read_text()
    assert "8007" not in near
    assert "0.6B" not in near
    assert "QWEN_FAST_URL" not in near


# ── Rerank purpose routing ──────────────────────────────────────────────────


def test_rerank_lane_urls():
    """2026-10-11: no local llama.cpp rerank lane exists; hosted 8B chain only."""
    from rugbrain.search import rerank as rr
    from rugbrain import rerank_lanes as rl

    assert not hasattr(rr, "RERANKER_URL_LIVE")
    assert ":8003" not in rr.RERANKER_URL and ":8008" not in rr.RERANKER_URL
    assert rl.MODEL == "Qwen/Qwen3-Reranker-8B"
    assert set(rl.DEFAULT_ORDER) == {"siliconflow", "openrouter", "deepinfra"}


def test_federated_route_documents_lanes():
    """The federated API surface exposes exactly the three documented lanes."""
    src = (
        Path(__file__).resolve().parents[1] / "src/rugbrain/routes/search_federated.py"
    ).read_text()
    for token in ("fast", "deep", "off"):
        assert token in src, f"federated route lost the '{token}' lane"


@pytest.mark.asyncio
async def test_rerank_deep_tier_used_for_big_corpus(monkeypatch):
    """deep lane resolves through the HOSTED 8B chain (operator directive 2026-10-11)."""
    from rugbrain.search import rerank as rr

    async def fake_hosted(query, valid, total):
        return [0.9] * total, "qwen3-reranker-8b"

    monkeypatch.setattr(rr, "_hosted_scores", fake_hosted)
    docs = [{"content": f"d{i}", "text": f"d{i}"} for i in range(5)]
    scores, method = await rr._score_candidates("q", docs, tier="deep")
    assert method == "qwen3-reranker-8b"
    assert any(s > 0 for s in scores)


async def test_rerank_live_lane_ladder_fast_then_deep(monkeypatch):
    """live lane: same hosted 8B chain; no dead local rungs remain."""
    from rugbrain.search import rerank as rr

    async def fake_hosted(query, valid, total):
        return [0.9] * total, "qwen3-reranker-8b"

    async def forbidden_local(*a, **k):  # noqa: ASYNC109
        raise AssertionError("the local :8003 rung must not be called")

    monkeypatch.setattr(rr, "_hosted_scores", fake_hosted)
    monkeypatch.setattr(rr, "_post_rerank", forbidden_local)
    docs = [{"content": f"d{i}", "text": f"d{i}"} for i in range(5)]
    scores, method = await rr._score_candidates("q", docs, tier="live")
    assert method == "qwen3-reranker-8b"
    assert any(s > 0 for s in scores)


def test_no_local_rerank_rung_in_source():
    """The dead local :8003 rung is gone from _score_candidates entirely."""
    src = (
        Path(__file__).resolve().parents[1] / "src/rugbrain/search/rerank.py"
    ).read_text()
    assert "_post_rerank(\n            RERANKER_URL" not in src
    assert "8B ONLY" in src


# ── Embed fallback ladder ORDER (auto mode) ─────────────────────────────────


@pytest.mark.asyncio
async def test_auto_ladder_order_deepinfra_then_int(monkeypatch):
    """auto tries DeepInfra → local :8004, in that order, fail-loud (2026-08-29)."""
    from rugbrain import embeddings as emb

    order: list[str] = []

    async def _di(text, dim=emb.EMBEDDING_DIM, query=False, timeout=30.0):  # noqa: ASYNC109 - mirrors prod signature
        order.append("deepinfra")
        return None

    async def _int(text, query=False, timeout=300.0):  # noqa: ASYNC109 - mirrors prod signature
        order.append("int")
        return None

    def _probe_sync():
        order.append("probe")
        return True

    async def _probe_async():
        order.append("probe")
        return True

    monkeypatch.setattr(emb, "_BACKEND", "auto")
    monkeypatch.setattr(emb, "_deepinfra_embed", _di)
    monkeypatch.setattr(emb, "_int_embed", _int)
    # rmi-rag probes sync (_int_reachable); rugbrain uses _int_reachable_async.
    # Exactly one of the two is invoked by the auto chain.
    monkeypatch.setattr(emb, "_int_reachable", _probe_sync)
    monkeypatch.setattr(emb, "_int_reachable_async", _probe_async, raising=False)

    with pytest.raises(emb.RAGUnavailableError):
        await emb._get_embedding_impl("ladder probe")

    assert order == ["deepinfra", "probe", "int"], order


@pytest.mark.asyncio
async def test_auto_ladder_deepinfra_succeeds_before_local(monkeypatch):
    """DeepInfra is the primary: a healthy remote lane short-circuits before
    the local probe is even reached."""
    from rugbrain import embeddings as emb

    order: list[str] = []

    async def _di(text, dim=emb.EMBEDDING_DIM, query=False, timeout=30.0):  # noqa: ASYNC109 - mirrors prod signature
        order.append("deepinfra")
        return [0.1] * emb.EMBEDDING_DIM

    def _reachable():
        order.append("probe")
        return True

    monkeypatch.setattr(emb, "_BACKEND", "auto")
    monkeypatch.setattr(emb, "_deepinfra_embed", _di)
    monkeypatch.setattr(emb, "_int_reachable", _reachable)

    vec = await emb._get_embedding_impl("ladder probe")
    assert order == ["deepinfra"], order
    assert len(vec) == emb.EMBEDDING_DIM


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

    async def _int(text, query=False, timeout=300.0):  # noqa: ASYNC109 - mirrors prod signature
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
        except Exception as exc:
            failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:80]}")

    await probe(
        "deep-embed :8004",
        "http://127.0.0.1:8004/v1/embeddings",
        {"model": "qwen3-embedding-8b", "input": ["tier smoke"]},
        lambda d: (lambda n: n == 2560 or failures.append(f"deep-embed dim {n} != 2560"))(
            len(d["data"][0]["embedding"])
        ),
    )
    await probe(
        "deep-rerank :8003",
        "http://127.0.0.1:8003/v1/rerank",
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

    assert not failures, "local-tier smoke failures:\n" + "\n".join(failures)
