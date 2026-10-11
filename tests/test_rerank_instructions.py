"""Tests for the vertical instruction library + noise-floor rerank behavior.

Pure-function coverage — no network. The instruction library encodes the
2026-08-22 finding that Qwen3-Reranker honors task framing embedded in the
query slot (our llama.cpp build ignores the /rerank 'instruct' field).
"""

from __future__ import annotations

import importlib

import pytest

rerank_mod = importlib.import_module("rugbrain.search.rerank")


class TestDetectVertical:
    def test_forensics_keywords(self):
        assert rerank_mod.detect_vertical("Is this token a rug pull?") == "forensics"
        assert rerank_mod.detect_vertical("check honeypot status of PEPE2") == "forensics"

    def test_tracing_keywords(self):
        assert rerank_mod.detect_vertical("trace fund flow from mixer wallet") == "tracing"

    def test_contract_keywords(self):
        assert (
            rerank_mod.detect_vertical("audit this solidity contract for reentrancy") == "contract"
        )

    def test_code_keywords(self):
        assert rerank_mod.detect_vertical("how to implement a token swap with web3.py") == "code"

    def test_generic_query_returns_none(self):
        assert rerank_mod.detect_vertical("what is the price of bitcoin") is None

    def test_forensics_wins_over_code(self):
        # Order matters: forensics patterns are checked first.
        assert rerank_mod.detect_vertical("code that detects honeypot scams") == "forensics"


class TestApplyInstruction:
    def test_instruction_embedded_for_vertical(self):
        out = rerank_mod.apply_instruction("is this token a rug pull?")
        assert out.startswith("[TASK] ")
        assert "[QUERY] is this token a rug pull?" in out
        assert "evidentiary value" in out

    def test_generic_query_unchanged(self):
        q = "what is the price of bitcoin"
        assert rerank_mod.apply_instruction(q) == q

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("RERANK_INSTRUCTIONS", "off")
        importlib.reload(rerank_mod)
        try:
            q = "is this token a rug pull?"
            assert rerank_mod.apply_instruction(q) == q
        finally:
            monkeypatch.delenv("RERANK_INSTRUCTIONS")
            importlib.reload(rerank_mod)

    def test_constant_prefix_per_vertical(self):
        """Same vertical => byte-identical prefix (prompt-cache friendly)."""
        a = rerank_mod.apply_instruction("rug pull check on token X")
        b = rerank_mod.apply_instruction("scam analysis for token Y")
        assert a.split("[QUERY]")[0] == b.split("[QUERY]")[0]


class TestNoiseFloor:
    def test_floor_constant_sane(self):
        # 0 < floor < 0.5: sub-0.1 is confidently irrelevant per sigmoid scores.
        assert 0.0 < rerank_mod._RERANK_NOISE_FLOOR < 0.5

    def test_text_limit_raised_for_ctx8192(self):
        # ctx bumped 4096 -> 8192 on 2026-08-22; limit must follow.
        assert rerank_mod._RERANK_TEXT_LIMIT >= 6000


@pytest.mark.asyncio
async def test_rerank_chunks_filters_noise_when_enough_strong(monkeypatch):
    """Sub-floor docs are dropped only when >= keep strong candidates remain."""
    chunks = [{"id": f"d{i}", "content": f"doc {i}", "score": 1.0 - i * 0.01} for i in range(6)]

    async def fake_score(query, candidates, tier="deep", **kwargs):
        # d0-d3 strong evidence, d4/d5 confident noise.
        scores = [0.95, 0.9, 0.85, 0.8, 0.05, 0.02]
        return scores[: len(candidates)], "qwen3-reranker-8b"

    monkeypatch.setattr(rerank_mod, "_score_candidates", fake_score)
    out = await rerank_mod.rerank_chunks("rug pull check", chunks, top_n=6, keep=4)
    assert [d["id"] for d in out] == ["d0", "d1", "d2", "d3"]
    assert all(d["reranked"] for d in out)


@pytest.mark.asyncio
async def test_rerank_chunks_keeps_full_set_when_signal_thin(monkeypatch):
    """Without enough above-floor candidates, nothing is dropped."""
    chunks = [{"id": f"d{i}", "content": f"doc {i}", "score": 1.0 - i * 0.01} for i in range(4)]

    async def fake_score(query, candidates, tier="deep", **kwargs):
        return [0.4, 0.08, 0.05, 0.02][: len(candidates)], "qwen3-reranker-8b"

    monkeypatch.setattr(rerank_mod, "_score_candidates", fake_score)
    out = await rerank_mod.rerank_chunks("obscure query", chunks, top_n=4, keep=4)
    assert len(out) == 4
