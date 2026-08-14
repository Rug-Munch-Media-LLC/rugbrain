"""Targeted tests for CODE-heavy retrieval improvements in ``rugbrain``.

Covers three behaviors added for smart-contract / crypto-code questions:

  * code-intent detection: the regex term tier (``function``, ``modifier``,
    ``abi``, ``sdk``, ``mev``, "how does X work") routes queries to code
    retrieval while exploit-framed queries ("reentrancy hack 2023") stay on
    the scam/exploit path;
  * code-chunk bias: code-carrying chunks float above prose for code queries
    and the answer path surfaces fenced code blocks intact (language-tagged);
  * chunker hardening: inline `` `code` `` spans (``_CODE_SPAN_RE``) are
    protected so a chunk boundary never splits mid-span.

All unit tests: no external services required.
"""

from __future__ import annotations

from rugbrain.app_chunking import _CODE_SPAN_RE, code_chunk, recursive_chunk
from rugbrain.search.engine import (
    CODING_COLLECTIONS,
    bias_toward_code_chunks,
    detect_coding_query,
    extract_code_blocks,
    format_code_answer,
    resolve_search_collections,
)
from rugbrain.search_service import search_coding_knowledge


# ── 1. code-intent detection (regex term tier) ─────────────────────────


def test_detect_coding_query_code_term_tier():
    # Terms the phrase hints alone did not cover.
    assert detect_coding_query("what is the ABI of a function")
    assert detect_coding_query("function selector collision")
    assert detect_coding_query("how does a modifier work")
    assert detect_coding_query("write a Python SDK wrapper")
    assert detect_coding_query("MEV bot sandwich example")
    assert detect_coding_query("what is the ABI")


def test_detect_coding_query_reentrancy_needs_code_context():
    # "reentrancy" alone (exploit framing) is NOT coding; paired with code
    # terms or a "how does" phrasing it is.
    assert not detect_coding_query("reentrancy hack 2023")
    assert detect_coding_query("how does reentrancy work")
    assert detect_coding_query("reentrancy attack code")


def test_detect_coding_query_preserves_non_code_negatives():
    assert not detect_coding_query("")
    assert not detect_coding_query("bitcoin price today")
    assert not detect_coding_query("ethereum whale activity")
    assert not detect_coding_query("how does the market work")
    # Scam/exploit phrasing must not flip to code routing.
    assert not detect_coding_query("what is a flash loan attack pattern")
    assert not detect_coding_query("exploit code")


def test_code_term_queries_route_to_coding_collections():
    routed = resolve_search_collections(None, "what is the ABI of a function")
    assert routed[: len(CODING_COLLECTIONS)] == list(CODING_COLLECTIONS)


# ── 2. code-chunk bias + answer formatting ─────────────────────────────


def test_bias_toward_code_chunks_orders_code_first_for_code_query():
    results = [
        {"content": "prose explanation without code", "similarity": 0.86},
        {"content": "contract Vault { function rescue() external {} }", "similarity": 0.85},
    ]
    biased = bias_toward_code_chunks(results, "how to fix reentrancy in Solidity")
    assert biased[0]["content"].startswith("contract Vault")


def test_bias_toward_code_chunks_passthrough_for_non_code_query():
    results = [
        {"content": "contract Vault { function rescue() external {} }", "similarity": 0.5},
        {"content": "general prose", "similarity": 0.9},
    ]
    biased = bias_toward_code_chunks(results, "bitcoin price today")
    assert biased == results


def test_bias_respects_doc_type_metadata():
    # A chunk typed as contract_code is code even when the marker regex
    # cannot tell from content alone.
    results = [
        {"content": "the vault was audited", "similarity": 0.9, "metadata": {}},
        {
            "content": "the vault was audited",
            "similarity": 0.88,
            "metadata": {"doc_type": "contract_code"},
        },
    ]
    biased = bias_toward_code_chunks(results, "how to implement a vault in Solidity")
    assert biased[0]["metadata"]["doc_type"] == "contract_code"


def test_extract_code_blocks_preserves_fence_and_language():
    content = (
        "Here is the fix:\n"
        "```solidity\n"
        "function rescue() external { owner.transfer(address(this).balance); }\n"
        "```\n"
        "Apply it in the vault."
    )
    blocks = extract_code_blocks(content)
    assert len(blocks) == 1
    assert blocks[0]["language"] == "solidity"
    assert blocks[0]["fence"] == "```"
    assert "function rescue() external" in blocks[0]["code"]


def test_format_code_answer_surfaces_code_block_intact():
    content = (
        "Fix with a guard:\n```solidity\nmodifier nonReentrant() { _; }\n```\nUse it on withdraw."
    )
    answer = format_code_answer(content)
    assert "```solidity" in answer
    assert "modifier nonReentrant() { _; }" in answer
    assert "```solidity\nmodifier nonReentrant() { _; }\n```" in answer
    # Prose without fenced code is returned unchanged.
    assert format_code_answer("just prose here") == "just prose here"


def test_search_coding_knowledge_surfaces_code_blocks():
    import asyncio

    import rugbrain.search.engine as engine_mod

    results = [
        {
            "content": (
                "Fix with a guard:\n"
                "```solidity\n"
                "modifier nonReentrant() { _; }\n"
                "```\n"
                "Use it on withdraw."
            ),
            "similarity": 0.9,
        },
        {"content": "prose about CEI ordering", "similarity": 0.8},
    ]

    async def fake_search(query, collections, limit=10, min_similarity=0.4):
        return [dict(r) for r in results]

    original = engine_mod.search_multi_collection
    engine_mod.search_multi_collection = fake_search
    try:
        out = asyncio.run(search_coding_knowledge("how to fix reentrancy in Solidity"))
        assert out["coding_knowledge"] is True
        code_res = [r for r in out["results"] if r.get("has_code_snippet")]
        assert len(code_res) == 1
        blocks = code_res[0]["code_blocks"]
        assert blocks and blocks[0]["language"] == "solidity"
        assert "modifier nonReentrant()" in blocks[0]["code"]
        assert "```solidity" in code_res[0]["code_answer"]
    finally:
        engine_mod.search_multi_collection = original


# ── 3. chunker hardening: inline code-span protection ──────────────────


def test_code_span_regex_defined_and_matches():
    assert _CODE_SPAN_RE.search("call `transfer` now") is not None
    assert _CODE_SPAN_RE.search("use ``msg.sender`` here") is not None
    # Triple fences are handled by the fence protection; unmatched backticks
    # never match.
    assert _CODE_SPAN_RE.search("```solidity") is None
    assert _CODE_SPAN_RE.search("a lone ` backtick") is None


def _assert_span_intact(chunks: list[str], span: str) -> None:
    assert any(span in c for c in chunks), f"{span!r} missing from chunks"
    mid = len(span) // 2
    assert not any(span[:mid] in c and span not in c for c in chunks), (
        f"{span!r} was split (head fragment) across a chunk boundary"
    )
    assert not any(span[-mid:] in c and span not in c for c in chunks), (
        f"{span!r} was split (tail fragment) across a chunk boundary"
    )


def test_recursive_chunk_keeps_inline_code_span_intact():
    para = (
        "Call `transfer` to move tokens; the `transfer` function reverts when "
        "the balance is insufficient and never re-enters the caller. "
        "Then call `withdraw` after state updates."
    )
    text = "\n\n".join([para] * 6)
    chunks = recursive_chunk(text, chunk_size=150, overlap=0.15)
    for span in ("`transfer`", "`withdraw`"):
        _assert_span_intact(chunks, span)


def test_code_chunk_keeps_inline_code_span_intact():
    src = (
        "// Use `deposit` for ETH inflow and `withdraw` for outflows.\n"
        "contract Vault {\n"
        "    function deposit() external payable { balances[msg.sender] += msg.value; }\n"
        "    function withdraw(uint256 amount) external { balances[msg.sender] -= amount; }\n"
        "}\n"
    )
    chunks = code_chunk(src, chunk_size=110, overlap=0.2)
    for span in ("`deposit`", "`withdraw`"):
        _assert_span_intact(chunks, span)
    # Function bodies stay atomic alongside the protected inline spans.
    for func in (
        "function deposit() external payable {",
        "function withdraw(uint256 amount) external {",
    ):
        assert not any(func[: len(func) // 2] in c and func not in c for c in chunks)
