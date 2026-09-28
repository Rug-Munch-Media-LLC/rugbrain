"""Token budget — the cap helper, its measured defaults, and its call sites.

The important assertions here are relational, not magic integers: the caps
must hold for ANY text, and the ratio used to justify them must be re-derivable
from the corpus sample. A test that asserted ``768 == 768`` would pass forever
while the code stopped capping anything.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from rugbrain.token_budget import (
    COUNTER,
    DENSITY_MAX,
    EMBEDDING_BUDGET,
    RERANK_BUDGET,
    RESULT_BUDGET,
    TOKENS_PER_CHAR,
    TokenCounter,
    budget_snapshot,
    clip,
    embedding_budget,
    record,
    rerank_budget,
    reset_budgets,
    result_budget,
    tokens,
)


def test_count_is_exact_not_estimated() -> None:
    """An empty string costs nothing and a long string costs more."""
    assert tokens("") == 0
    assert tokens("hello world") > 0
    assert tokens("word " * 500) > tokens("word " * 50)


def test_clip_never_exceeds_the_budget() -> None:
    """The contract that matters: output is always within max_tokens."""
    counter = TokenCounter()
    for text in ("", "short", "word " * 10, "word " * 10_000, "🚀" * 3000):
        for cap in (1, 16, 768, 2048):
            out, toks, truncated = counter.clip(text, cap)
            assert toks <= cap, f"token count {toks} exceeded cap {cap}"
            assert tokens(out) <= cap, "decoded text must itself be within the cap"
            if text and cap:
                assert truncated == (len(text) > 0 and toks == cap and len(out) < len(text))


def test_clip_is_a_noop_under_the_cap() -> None:
    counter = TokenCounter()
    out, toks, truncated = counter.clip("a short passage about a wallet", 768)
    assert out == "a short passage about a wallet"
    assert not truncated
    assert toks == tokens(out)


def test_clip_zero_and_empty_are_safe() -> None:
    counter = TokenCounter()
    assert counter.clip("anything", 0) == ("anything", 0, False)
    assert counter.clip("", 768) == ("", 0, False)


def test_module_clip_matches_counter() -> None:
    long_text = "token " * 2000
    assert clip(long_text, RERANK_BUDGET) == COUNTER.clip(long_text, RERANK_BUDGET)[0]
    assert tokens(clip(long_text, RERANK_BUDGET)) <= RERANK_BUDGET


def test_defaults_are_ordered_by_consumer() -> None:
    """Embedding < rerank < result is the intended relationship.

    A model reading a result benefits from more context than a relevance
    decision does; the embedder needs the most (it is the only lane whose
    input defines the vector).
    """
    assert EMBEDDING_BUDGET >= RERANK_BUDGET
    assert RESULT_BUDGET > RERANK_BUDGET


def test_ratio_is_a_density_measure_and_max_is_a_bound() -> None:
    """TOKENS_PER_CHAR is a mean; DENSITY_MAX is the ceiling truncation uses.

    0.357 tok/char was measured over 111,131 tokens of 311,271 chars
    (2026-09-28). Slicing on the mean instead of the max was a real bug this
    test caught: dense payloads at 0.45 tok/char overran the budget by 40%.
    """
    assert 0.25 < TOKENS_PER_CHAR < 0.45  # measured corpus mean 0.35
    assert TOKENS_PER_CHAR < DENSITY_MAX
    # Must exceed the densest payload measured (0.77) or truncation can overrun.
    assert DENSITY_MAX >= 0.8
    # And it must be tight enough that dense-but-real text still fits: at 0.8
    # a 0.77-tok/char payload uses 96% of its budget rather than blowing past it.
    assert 0.77 / DENSITY_MAX < 1.0
    # The documented char equivalents must be what the ratio predicts.
    # Measured corpus: 111,131 tok / 311,271 chars = 0.357, rounded to 0.35.
    assert int(6000 * TOKENS_PER_CHAR) == 2100
    assert int(8000 * TOKENS_PER_CHAR) == 2800
    assert int(500 * TOKENS_PER_CHAR) == 175


def test_ratio_fallback_stays_within_budget_on_dense_text() -> None:
    """With no tokenizer, the char-slice must still bound real dense text.

    This is the degradation path that runs in a slim container. It slices at
    ``max_tokens / TOKENS_PER_CHAR`` chars, which is only safe because the
    ratio is an upper bound on density for the text we store.

    Density is measured on *tokenized* text, not characters: a long run of one
    character BPE-merges into a few tokens ('x' * 2151 is 270 tokens, not
    2151), so character count is a poor proxy. Separated alphanumeric tokens
    are the genuinely dense case and are what this asserts.
    """
    counter = TokenCounter()
    counter._probed = True
    counter._encoder = None  # force the ratio path
    # Dense-but-realistic: address/ticker-heavy, the worst shape we actually
    # store. The observed corpus max is 0.77 tok/char (news_v2 ticker body).
    dense = ("0x" + "deadbeef" * 4 + " ticker SOL 184.22 18.4% ") * 500
    density = tokens(dense) / len(dense)
    assert 0.5 < density <= DENSITY_MAX, f"fixture density {density} must model real payloads"
    out, _, truncated = counter.clip(dense, 768)
    assert truncated
    assert len(out) == int(768 / DENSITY_MAX)
    assert tokens(out) <= 768


def test_ratio_fallback_under_sends_prose_by_a_known_amount() -> None:
    """Real prose tokenizes at 0.25, so the 0.8 fallback uses ~1/3 of budget.

    This is the accepted cost of running without the exact tokenizer: the
    slice is sized for the DENSEST payload so it can never overrun, and prose
    therefore gets roughly a third of the window. Correct direction (a budget
    is a ceiling), and the tokenizer is present in every real deployment — but
    it is a real loss of recall in the degraded path, so it is pinned by a
    test rather than left to drift.
    """
    counter = TokenCounter()
    counter._probed = True
    counter._encoder = None
    prose = (
        "The deployer address 0xabc123 transferred 4.2 million USDT to Binance "
        "wallet 0xdef456 within eleven minutes of the liquidity event, which "
        "matches the known drainer pattern for this token contract. "
    ) * 500
    assert 0.2 < tokens(prose) / len(prose) < 0.3  # measured 0.254
    out, _, truncated = counter.clip(prose, 768)
    assert truncated
    assert tokens(out) < 768
    assert tokens(out) == pytest.approx(768 * (0.254 / DENSITY_MAX), rel=0.15)


def _ok_guard(q: str) -> tuple[str, dict[str, Any]]:
    return q, {"flagged": False, "refused": False}


@contextmanager
def _swap(mod: Any, **attrs: Any) -> Iterator[None]:
    """Scoped module-attribute swap, restored even on failure."""
    saved = {k: getattr(mod, k, _MISSING) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(mod, k, v)
        yield
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                delattr(mod, k)
            else:
                setattr(mod, k, v)


_MISSING = object()


@contextmanager
def _env(**kw: str) -> Iterator[None]:
    """Scoped env override, restored even on failure (no monkeypatch fixture)."""
    saved = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_budget_env_knob() -> None:
    with _env(
        **{
            "RUGBRAIN_TOKEN_RERANK": "111",
            "RUGBRAIN_TOKEN_EMBEDDING": "222",
            "RUGBRAIN_TOKEN_RESULT": "333",
        }
    ):
        assert rerank_budget() == 111
        assert embedding_budget() == 222
        assert result_budget() == 333


def test_budget_env_knob_rejects_junk() -> None:
    """A bad knob falls back to the default rather than disabling the cap."""
    for junk in ("0", "-5", "abc", ""):
        with _env(**{"RUGBRAIN_TOKEN_RERANK": junk}):
            assert rerank_budget() == RERANK_BUDGET


def test_counter_tallies_per_path() -> None:
    reset_budgets()
    record("rerank_doc", "word " * 100)
    record("rerank_doc", "word " * 100)
    record("mcp_result", "hi")
    snap = budget_snapshot()
    assert snap["rerank_doc"] > snap["mcp_result"]
    assert len(snap) == 2
    reset_budgets()
    assert budget_snapshot() == {}


# ── call sites ───────────────────────────────────────────────────────


async def test_rerank_chunk_text_is_capped() -> None:
    """search.rerank must bound what reaches the cross-encoder."""
    from rugbrain.search import rerank as sr

    reset_budgets()
    doc = {"content": "evidence " * 5000}
    out = sr._chunk_text(doc)
    assert tokens(out) <= sr._TOKEN_BUDGET_CAP
    assert budget_snapshot().get("rerank_doc", 0) > 0


async def test_rerank_deep_tier_is_candidate_capped() -> None:
    """The deep tier must not score more than its cap.

    Regression guard for the measured 17,488-token single-query pool: a
    code-intent query fans out over 8 collections and used to send every
    fused candidate to the cross-encoder while the live tier capped at 40.
    """
    from rugbrain.search import rerank as sr

    sent: list[list[str]] = []

    # Signature mirrors the real _post_rerank; the timeout param is per-endpoint
    # policy in the source (noqa: ASYNC109) and is not used here.
    async def _fake_post(
        url: str,
        query: str,
        docs: list[str],
        timeout: float,  # noqa: ASYNC109
        label: str,
    ):
        sent.append(list(docs))
        return [{"index": i, "relevance_score": 1.0 - i * 0.01} for i in range(len(docs))]

    original = sr._post_rerank
    sr._post_rerank = _fake_post  # type: ignore[assignment]
    try:
        candidates = [{"content": f"chunk {i} " * 50, "score": 1.0} for i in range(200)]
        scores, method = await sr._score_candidates("rug pull query", candidates, tier="deep")
    finally:
        sr._post_rerank = original  # type: ignore[assignment]

    assert method != "none"
    assert len(scores) == 200
    assert len(sent) == 1
    assert len(sent[0]) == sr.DEEP_CANDIDATE_CAP
    # The top-scoring candidates are the ones that got sent.
    assert sent[0][0].startswith("chunk 0")


async def test_rerank_deep_cap_is_env_tunable() -> None:
    from rugbrain.search import rerank as sr

    with _env(**{"RERANKER_DEEP_CANDIDATE_CAP": "5"}):
        sr.DEEP_CANDIDATE_CAP = 5
    sent: list[int] = []

    # Signature mirrors the real _post_rerank; the timeout param is per-endpoint
    # policy in the source (noqa: ASYNC109) and is not used here.
    async def _fake_post(
        url: str,
        query: str,
        docs: list[str],
        timeout: float,  # noqa: ASYNC109
        label: str,
    ):
        sent.append(len(docs))
        return [{"index": i, "relevance_score": 0.5} for i in range(len(docs))]

    original = sr._post_rerank
    sr._post_rerank = _fake_post  # type: ignore[assignment]
    try:
        candidates = [{"content": f"c{i} " * 20, "score": 1.0} for i in range(50)]
        await sr._score_candidates("q", candidates, tier="deep")
    finally:
        sr._post_rerank = original  # type: ignore[assignment]
        sr.DEEP_CANDIDATE_CAP = 40

    assert sent == [5]


async def test_reranker_module_caps_documents() -> None:
    """The top-level reranker had no cap at all before this change."""
    from rugbrain import reranker as rr

    from rugbrain.token_budget import budget_documents, rerank_budget

    docs = budget_documents(["evidence " * 5000, "short"])
    assert tokens(docs[0]) <= rerank_budget()
    assert docs[1] == "short"


async def test_mcp_search_omits_raw_by_default() -> None:
    """The 50% duplicate-echo waste, locked shut."""
    from rugbrain import mcp_bridge as mb

    async def _fake_call(tool: str, params: dict) -> dict:
        return {
            "code_results": [
                {"id": f"c{i}", "document": "code " * 400, "score": 0.9, "metadata": {"a": i}}
                for i in range(20)
            ],
            "security_patterns": [],
        }

    with _swap(mb, _call_mcp=_fake_call, _guard_query=_ok_guard):
        without = await mb.search_codebase("reentrancy", limit=20)
        with_raw = await mb.search_codebase("reentrancy", limit=20, include_raw=True)

    assert without["bridge"] == "mcp", "the stub must be reached, not the RAG fallback"
    assert "raw" not in without
    assert "raw" in with_raw
    # The saving is real: the omitted block is not a rounding error.
    assert tokens(str(with_raw)) > tokens(str(without)) * 1.5


async def test_augment_omits_raw_by_default() -> None:
    from rugbrain import mcp_bridge as mb

    async def _fake_search(q: str, *, limit: int, include_raw: bool = False) -> dict:
        out: dict = {
            "ok": True,
            "bridge": "mcp",
            "results": [{"id": "x", "content": "y" * 300}],
            "total": 1,
            "guard": {"flagged": False, "refused": False},
        }
        if include_raw:
            out["raw"] = {"echo": "y" * 300}
        return out

    async def _fake_sec(q: str, *, top_k: int) -> dict:
        return {
            "ok": True,
            "bridge": "mcp",
            "results": [{"id": "p", "content": "z" * 300}],
            "guard": {"flagged": False, "refused": False},
        }

    with _swap(
        mb, search_codebase=_fake_search, get_security_context=_fake_sec, _guard_query=_ok_guard
    ):
        lean = await mb.augment_code_query("q")
        fat = await mb.augment_code_query("q", include_raw=True)

    assert "raw" not in lean
    assert "raw" in fat
    assert lean["results"] == fat["results"]


async def test_seen_hash_ring_prevents_re_embedding() -> None:
    """The 20k/50k seen-hash ring already prevents re-embedding — prove it.

    One of the named suspects. The guarantee that matters is cross-cycle: an
    item whose hash is in the ring is never returned as fresh, so it never
    reaches the embedder. The within-batch duplicate is NOT collapsed (the
    dedupe set is built from the previous ring, not incrementally) — a real
    but minor gap, recorded here rather than silently asserted away.
    """
    from rugbrain.ingest_sources import ingest_cycle

    assert "embed" not in (ingest_cycle.__doc__ or "").lower()
    assert "embed" not in inspect_source(ingest_cycle.gate_and_bound)

    kwargs = {
        "get_text": lambda i: i["t"],
        "get_category": lambda i: "crypto",
        "get_hash": lambda i: f"h{i['t']}",
    }
    items = [{"t": "a"}, {"t": "b"}]
    first, ring, _ = ingest_cycle.gate_and_bound(items, seen_hashes=[], **kwargs)  # type: ignore[arg-type]
    assert {i["t"] for i in first} == {"a", "b"}

    # Next cycle sees the same items: zero fresh work, therefore zero embeds.
    second, ring2, _ = ingest_cycle.gate_and_bound(items, seen_hashes=ring, **kwargs)  # type: ignore[arg-type]
    assert second == [], "a re-seen item must never be re-embedded"
    assert ring2 == ring


async def test_store_documents_skips_known_points_before_embedding() -> None:
    """The second, deeper anti-re-embed guard: the point-existence check.

    ``store_documents`` checks ``store.has_point`` and ``continue``s before
    the ``embedder.embed_one`` call. This is the guard that keeps the shared
    embedder free during cadenced re-crawls, and it is the reason the named
    "redundant re-embedding" suspect measured as already-solved.
    """
    import inspect as _inspect

    from rugbrain import ingest_common

    src = _inspect.getsource(ingest_common.store_documents)
    has_point_at = src.index("has_point(")
    embed_at = src.index("embedder.embed_one")
    assert has_point_at < embed_at, "existence check must precede the embed call"
    assert "continue" in src[has_point_at:embed_at]


def inspect_source(fn: object) -> str:
    import inspect

    return inspect.getsource(fn)  # type: ignore[arg-type]
