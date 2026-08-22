"""Tests for the RugBrain coding-knowledge expansion.

Covers the deterministic, pure parts — code-intent query detection, collection
routing, doc parsing/builders — so they can be verified without live
Qdrant/Qwen3. End-to-end verification runs via scripts/verify_coding_
knowledge.py.
"""

from __future__ import annotations

import os
import sys
from typing import Any

# scripts/ dir on sys.path so the coding-knowledge modules import cleanly.
_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from coding_ingest_common import (  # noqa: E402
    build_coding_doc,
    html_to_text,
    markdown_sections,
    rst_to_text,
)

from rugbrain.search.engine import (  # noqa: E402
    CODING_COLLECTIONS,
    SCAM_COLLECTIONS,
    detect_coding_query,
    resolve_search_collections,
)
from rugbrain.search_service import search_coding_knowledge  # noqa: E402

# ── Code-intent detection ─────────────────────────────────────────────


def test_detect_coding_query_acceptance_queries() -> None:
    assert detect_coding_query("how to implement reentrancy guard in Solidity")
    assert detect_coding_query("Solana PDA security best practices")
    assert detect_coding_query("Move vs Solidity resource model")
    assert detect_coding_query("write a secure ERC20 token")
    assert detect_coding_query("Rust Solana program example with Anchor")


def test_detect_coding_query_negatives() -> None:
    assert not detect_coding_query("")
    assert not detect_coding_query("bitcoin price today")
    assert not detect_coding_query("ethereum whale activity")
    # Bare "reentrancy" (an exploit query) should NOT route as coding.
    assert not detect_coding_query("reentrancy hack 2023")


def test_detect_coding_query_case_insensitive() -> None:
    assert detect_coding_query("SOLIDITY STORAGE LAYOUT")
    assert detect_coding_query("How To Implement A Vault In Solidity")


# ── Collection routing ────────────────────────────────────────────────


def test_resolve_collections_explicit_wins() -> None:
    assert resolve_search_collections(["threat_intel"], "solidity") == ["threat_intel"]


def test_resolve_collections_coding_priority() -> None:
    routed = resolve_search_collections(None, "how to implement reentrancy guard in Solidity")
    assert routed[0] == "coding_knowledge"
    assert routed[: len(CODING_COLLECTIONS)] == list(CODING_COLLECTIONS)


def test_resolve_collections_scam_still_routes() -> None:
    routed = resolve_search_collections(None, "how was Euler hacked")
    assert routed[0] == "defi_exploits"
    assert routed[: len(SCAM_COLLECTIONS)] == list(SCAM_COLLECTIONS)


def test_resolve_collections_default() -> None:
    from rugbrain.search.engine import COLLECTIONS

    routed = resolve_search_collections(None, "general market question")
    assert routed == list(COLLECTIONS)


def test_coding_collections_defined() -> None:
    assert "coding_knowledge" in CODING_COLLECTIONS
    assert "solidity_patterns" in CODING_COLLECTIONS
    assert "solana_patterns" in CODING_COLLECTIONS


# ── Search function surface ───────────────────────────────────────────


def test_search_coding_knowledge_exists() -> None:
    import inspect

    sig = inspect.signature(search_coding_knowledge)
    assert "query" in sig.parameters
    assert "collections" in sig.parameters


def test_search_coding_knowledge_flags_code() -> Any:
    results = [
        {
            "content": "contract Vault { function withdraw() external { } }",
            "similarity": 0.9,
        },
        {"content": "just an explanation without code", "similarity": 0.8},
    ]
    # Patch the underlying search so no live Qdrant is required.
    import rugbrain.search.engine as engine_mod

    async def fake_search(query: Any, collections: Any, limit: int = 10, min_similarity: float = 0.4) -> Any:
        return list(results)

    original = engine_mod.search_multi_collection
    engine_mod.search_multi_collection = fake_search
    try:
        import asyncio

        out = asyncio.run(search_coding_knowledge("reentrancy guard in Solidity"))
        assert out["coding_knowledge"] is True
        flagged = [r for r in out["results"] if r.get("has_code_snippet")]
        assert len(flagged) == 1
        assert flagged[0]["content"].startswith("contract Vault")
    finally:
        engine_mod.search_multi_collection = original


# ── Doc builders ──────────────────────────────────────────────────────


def test_build_coding_doc_shape() -> None:
    doc = build_coding_doc(
        source="test-source",
        rel_key="topic/abc",
        content="some Solidity knowledge",
        ecosystem="ethereum",
        language="solidity",
        knowledge_type="language_docs",
        topic="reentrancy",
        title="Test",
        url="https://example.com/x",
    )
    assert doc["rel_key"] == "coding:test-source:topic/abc"
    assert doc["collection"] == "coding_knowledge"
    assert doc["chain"] == "ethereum"
    assert doc["metadata"]["ecosystem"] == "ethereum"
    assert doc["metadata"]["language"] == "solidity"
    assert doc["metadata"]["knowledge_type"] == "language_docs"
    assert doc["metadata"]["coding_knowledge"] is True
    assert doc["metadata"]["source_url"] == "https://example.com/x"


# ── Parsing helpers ───────────────────────────────────────────────────


def test_markdown_sections_heading_split() -> None:
    text = "# Reentrancy\n\nUse CEI ordering.\n\n## Withdrawal\n\nUse pull payments."
    sections = markdown_sections(text, min_len=1)
    assert len(sections) == 2
    assert sections[0]["heading"] == "Reentrancy"
    assert "CEI" in sections[0]["text"]
    assert sections[1]["heading"] == "Withdrawal"


def test_markdown_sections_empty() -> None:
    assert markdown_sections("") == []
    assert markdown_sections("   ") == []


def test_markdown_sections_long_split() -> None:
    text = "# Big\n\n" + "\n\n".join(f"paragraph {i} " + "x" * 100 for i in range(50))
    sections = markdown_sections(text, max_len=1000)
    assert len(sections) > 1
    assert all(len(s["text"]) <= 1050 for s in sections)


def test_html_to_text_strips_tags() -> None:
    html = (
        "<html><head><script>var x = 1;</script></head><body>"
        "<h1>Reentrancy Guard</h1><p>The <code>nonReentrant</code> modifier "
        "prevents attacks.</p><pre>function f() {}</pre></body></html>"
    )
    text = html_to_text(html)
    assert "Reentrancy Guard" in text
    assert "nonReentrant" in text
    assert "var x = 1" not in text
    assert "<h1>" not in text


def test_rst_to_text_strips_directives() -> None:
    rst = (
        "Security Considerations\n"
        "=======================\n"
        "\n"
        ".. warning::\n"
        "   Be careful with external calls.\n"
        "\n"
        "Use ``msg.sender`` not ``tx.origin``.\n"
    )
    text = rst_to_text(rst)
    assert "msg.sender" in text
    assert "tx.origin" in text
    assert ".. warning::" not in text
