"""HyDE + multi-query expansion contracts (async LLM paths, untested).

* hyde_expand: LLM doc used when valid; query fallback when LLM absent/
  fails/returns junk; embedding computed from the FINAL doc
* multi_query_expand: list rewrites deduped (original first), capped at
  MAX_MULTI_QUERY_VARIANTS; comma/semicolon string split; no-LLM →
  [query]; junk filtered
"""

from __future__ import annotations

import pytest

from rugbrain.search.query_transform import (
    MAX_MULTI_QUERY_VARIANTS,
    hyde_expand,
    multi_query_expand,
)


@pytest.mark.asyncio
async def test_hyde_uses_llm_document():
    async def embed(text):
        return [0.1] * 8

    async def llm(q):
        return "A hypothetical answer about rug pulls and drained liquidity."

    out = await hyde_expand("rug pull?", embed, llm)
    assert out["used_llm"] is True
    assert "drained liquidity" in out["hypothetical_doc"]
    assert len(out["embedding"]) == 8


@pytest.mark.asyncio
async def test_hyde_fallback_to_query_without_llm():
    captured = {}

    async def embed(text):
        captured["text"] = text
        return [0.2] * 4

    out = await hyde_expand("honeypot check", embed, None)
    assert out["used_llm"] is False
    assert out["hypothetical_doc"] == "honeypot check"
    assert captured["text"] == "honeypot check"  # embeds the QUERY itself


@pytest.mark.asyncio
async def test_hyde_fallback_on_llm_junk():
    async def embed(text):
        return [0.3] * 4

    for junk in ("   ", 42, None):
        out = await hyde_expand("q", embed, lambda q: junk)
        assert out["used_llm"] is False
        assert out["hypothetical_doc"] == "q"


@pytest.mark.asyncio
async def test_multi_query_dedupes_and_caps():
    async def llm(q):
        return [
            "variant one",
            "variant two",
            "variant one",
            "variant three",
            "variant four",
            "variant five",
        ]

    out = await multi_query_expand("base query", llm)
    assert out[0] == "base query"  # original first
    assert len(out) <= MAX_MULTI_QUERY_VARIANTS
    assert len(out) == len(set(out))  # no duplicates


@pytest.mark.asyncio
async def test_multi_query_splits_string():
    async def llm(q):
        return "rewrite A; rewrite B, rewrite C"

    out = await multi_query_expand("base", llm)
    assert "rewrite A" in out and "rewrite B" in out and "rewrite C" in out


@pytest.mark.asyncio
async def test_multi_query_no_llm_returns_original():
    out = await multi_query_expand("just this", None)
    assert out == ["just this"]
