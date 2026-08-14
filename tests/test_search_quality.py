"""Search-quality tests for RugBrain code queries.

Verifies the three acceptance code questions route to the right collections
and that the RAG answers them with code-first, security-aware content —
without requiring live Qdrant/Infinity (search results are injected via a
mocked ``search_multi_collection``).

The three acceptance queries (Part 3 of the coding-knowledge expansion):
  1. "how to implement reentrancy guard in Solidity"
  2. "Solana PDA security best practices"
  3. "what is a flash loan attack pattern"

Live end-to-end verification (with real Qdrant + Infinity) is provided by
``scripts/verify_coding_knowledge.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import rugbrain.search.engine as engine_mod  # noqa: E402
from rugbrain.search.engine import (  # noqa: E402
    CODING_COLLECTIONS,
    SCAM_COLLECTIONS,
    detect_coding_query,
    detect_scam_query,
    resolve_search_collections,
)
from rugbrain.search_service import search_coding_knowledge, search_scam_knowledge  # noqa: E402

# Acceptance queries from the spec.
REENTRANCY_QUERY = "how to implement reentrancy guard in Solidity"
PDA_QUERY = "Solana PDA security best practices"
FLASH_LOAN_QUERY = "what is a flash loan attack pattern"

# Mock corpus — content answers that should match each acceptance query.
MOCK_RESULTS: dict[str, list[dict[str, object]]] = {
    REENTRANCY_QUERY: [
        {
            "content": (
                "OpenZeppelin's ReentrancyGuard prevents reentrancy with a nonReentrant "
                "modifier. Always follow checks-effects-interactions (CEI): validate state, "
                "update state, then make external calls."
            ),
            "similarity": 0.91,
            "source": "coding:openzeppelin-docs",
            "metadata": {"language": "solidity", "ecosystem": "ethereum"},
        },
        {
            "content": "pragma solidity ^0.8.20;\ncontract Vault { function withdraw() external nonReentrant {} }",
            "similarity": 0.87,
            "source": "coding:solidity-by-example",
            "metadata": {"language": "solidity"},
        },
    ],
    PDA_QUERY: [
        {
            "content": (
                "PDA security: always validate the bump byte is canonical (seeds.find in "
                "Anchor), never trust seeds from instruction data, and use PDA-derived "
                "signer seeds to scope authority."
            ),
            "similarity": 0.89,
            "source": "coding:anchor",
            "metadata": {"language": "rust", "ecosystem": "solana"},
        },
        {
            "content": "pub fn initialize(ctx: Context<Initialize>) -> Result<()> {\n    let (pda, bump) = Pubkey::find_program_address(&[seed], program_id);\n}",
            "similarity": 0.84,
            "source": "coding:solana-labs-solana",
            "metadata": {"language": "rust"},
        },
    ],
    FLASH_LOAN_QUERY: [
        {
            "content": (
                "A flash loan attack pattern: an attacker borrows unbacked liquidity from "
                "the lender in the same transaction, manipulates an oracle or drains a pool "
                "inside the callback, repays the loan, and keeps the profit."
            ),
            "similarity": 0.9,
            "source": "scam:defi_exploits",
            "metadata": {"category": "flash-loan"},
        },
        {
            "content": "flash loan callback onFlashLoan reentrancy oracle manipulation repayment",
            "similarity": 0.83,
            "source": "scam:scam_patterns",
            "metadata": {"category": "flash-loan"},
        },
    ],
}


def _install_mock(query: str, results: list[dict[str, object]]) -> None:
    """Patch search_multi_collection to return *results* for *query*."""

    async def fake_search(q, collections=None, limit=10, min_similarity=0.4):
        return [dict(r) for r in results if q == query]

    engine_mod.search_multi_collection = fake_search


def _uninstall_mock() -> None:
    import importlib

    importlib.reload(engine_mod)


# ── Query routing ───────────────────────────────────────────────────────


def test_reentrancy_query_routes_to_coding():
    assert detect_coding_query(REENTRANCY_QUERY)
    routed = resolve_search_collections(None, REENTRANCY_QUERY)
    assert routed[0] == "coding_knowledge"
    assert routed[: len(CODING_COLLECTIONS)] == list(CODING_COLLECTIONS)


def test_pda_query_routes_to_coding():
    assert detect_coding_query(PDA_QUERY)
    routed = resolve_search_collections(None, PDA_QUERY)
    assert routed[0] == "coding_knowledge"


def test_flash_loan_query_routes_to_scam_knowledge():
    # "flash loan" + "attack" are scam/exploit hints; the query must route to
    # the exploit knowledge base so RugBrain answers with attack specifics.
    assert detect_scam_query(FLASH_LOAN_QUERY)
    routed = resolve_search_collections(None, FLASH_LOAN_QUERY)
    assert routed[0] == "defi_exploits"
    assert routed[: len(SCAM_COLLECTIONS)] == list(SCAM_COLLECTIONS)


# ── Answer quality (mocked search) ─────────────────────────────────────


def test_reentrancy_answer_has_code_and_cei():
    _install_mock(REENTRANCY_QUERY, MOCK_RESULTS[REENTRANCY_QUERY])
    try:
        out = asyncio.run(search_coding_knowledge(REENTRANCY_QUERY))
        assert out["coding_knowledge"] is True
        assert out["total"] == 2
        combined = " ".join(r.get("content", "") for r in out["results"]).lower()
        assert "nonreentrant" in combined
        assert "checks-effects" in combined or "cei" in combined
        assert "openzeppelin" in combined
        flagged = [r for r in out["results"] if r.get("has_code_snippet")]
        assert len(flagged) == 1
        assert flagged[0]["content"].startswith("pragma solidity")
    finally:
        _uninstall_mock()


def test_pda_answer_has_anchor_and_bump_security():
    _install_mock(PDA_QUERY, MOCK_RESULTS[PDA_QUERY])
    try:
        out = asyncio.run(search_coding_knowledge(PDA_QUERY))
        combined = " ".join(r.get("content", "") for r in out["results"]).lower()
        assert "pda" in combined
        assert "bump" in combined
        assert "anchor" in combined
        assert "seed" in combined
    finally:
        _uninstall_mock()


def test_flash_loan_answer_explains_attack_pattern():
    _install_mock(FLASH_LOAN_QUERY, MOCK_RESULTS[FLASH_LOAN_QUERY])
    try:
        out = asyncio.run(search_scam_knowledge(FLASH_LOAN_QUERY))
        assert out["scam_knowledge"] is True
        combined = " ".join(r.get("content", "") for r in out["results"]).lower()
        assert "flash loan" in combined
        assert "callback" in combined
        assert "oracle" in combined
        assert "repay" in combined
    finally:
        _uninstall_mock()


# ── Codebase MCP bridge surface (no live MCP needed) ──────────────────


def test_mcp_bridge_imports_and_functions_exist():
    import inspect

    from rugbrain.mcp_bridge import (
        get_security_context,
        search_codebase,
        trace_codebase,
    )

    for fn in (search_codebase, trace_codebase, get_security_context):
        assert inspect.iscoroutinefunction(fn)
    sig = inspect.signature(search_codebase)
    assert "query" in sig.parameters
    sig = inspect.signature(trace_codebase)
    assert "function" in sig.parameters


def test_mcp_bridge_fallback_shapes():

    # Without touching a live MCP, confirm the fallback result shape keys.
    from rugbrain.mcp_bridge import _fallback_search, _fallback_security, _fallback_trace

    out = asyncio.run(_fallback_search(REENTRANCY_QUERY, limit=3))
    assert out["bridge"] == "rag_fallback"
    assert out["tool"] == "search_codebase"
    assert "results" in out

    sec = asyncio.run(_fallback_security(FLASH_LOAN_QUERY, top_k=3))
    assert sec["bridge"] == "rag_fallback"
    assert sec["tool"] == "get_security_context"

    tr = asyncio.run(_fallback_trace("swap", depth=2))
    assert tr["bridge"] == "rag_fallback"
    assert tr["backend"] == "rag_fallback"
