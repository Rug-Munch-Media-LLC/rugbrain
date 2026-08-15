"""Tests for prompt compression + "lost in the middle" mitigation.

Covers the two public capabilities of ``rugbrain.prompt_compression``:

* ``compress_context`` — LLMLingua-style compression with the extractive
  fallback (keeps code blocks / addresses intact, drops filler).
* ``layout_context`` / ``pack_context`` — query-last inversion that defeats
  "lost in the middle" for small context windows.

All compression tests force the extractive path (``use_llmlingua=False`` or
``LLMLINGUA_AVAILABLE=False``) so they are deterministic and pass in
environments with or without the optional ``llmlingua`` package.
"""

import pytest

from rugbrain.prompt_compression import (
    compress_context,
    layout_context,
    pack_context,
)

ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
SECOND_ADDRESS = "0xabcdef1234567890abcdef1234567890abcdef12"
CODE_BLOCK = (
    "```solidity\n"
    "function withdraw(uint256 amount) external {\n"
    '    require(balances[msg.sender] >= amount, "insufficient");\n'
    '    (bool ok,) = msg.sender.call{value: amount}("");\n'
    '    require(ok, "transfer failed");\n'
    "}\n"
    "```"
)


@pytest.fixture
def sample_chunks():
    """Three chunks: high-signal prose, code, and filler-heavy prose."""
    return [
        {
            "id": "1",
            "content": (
                f"The reentrancy vulnerability in contract {ADDRESS} allowed the "
                "attacker to drain 3.6M ETH from the DAO treasury."
            ),
        },
        {
            "id": "2",
            "content": (
                f"{CODE_BLOCK}\nThis pattern is vulnerable to reentrancy because "
                "the state update happens after the external call."
            ),
        },
        {
            "id": "3",
            "content": (
                "It is important to note that however, we can see the token was "
                "deployed by address 0xabcdef1234567890abcdef1234567890abcdef12 "
                "and the honeypot detected a drain of $250k. Stay tuned for more "
                "updates!"
            ),
        },
    ]


def test_module_imports_cleanly():
    """Module must import without llmlingua installed (lazy optional import)."""
    import rugbrain.prompt_compression  # noqa: F401


def test_compress_context_reduces_tokens_and_keeps_code_and_addresses(sample_chunks):
    """Extractive compression: reduces tokens, keeps code + addresses intact."""
    out = compress_context(sample_chunks, target_tokens=60, use_llmlingua=False)

    assert len(out) == 3
    for chunk in out:
        assert chunk["compression_method"] == "extractive"
        assert chunk["tokens_after"] < chunk["tokens_before"]

    # Addresses must survive byte-for-byte (never split by sentence boundaries).
    assert ADDRESS in out[0]["content"]
    assert SECOND_ADDRESS in out[2]["content"]
    # Code block head must survive with its fence intact.
    assert "```solidity" in out[1]["content"]
    # Kept units are tagged.
    assert "address" in out[0]["compression_tags"]
    assert "code" in out[1]["compression_tags"]


def test_compress_context_prefers_high_signal_over_filler(sample_chunks):
    """Filler ('Stay tuned!') must not outrank the high-signal sentence."""
    out = compress_context(sample_chunks, target_tokens=60, use_llmlingua=False)

    filler = "Stay tuned for more updates!"
    # The filler sentence must not survive compression, or if it does it must
    # not be the ONLY surviving content of its chunk.
    for chunk in out:
        if filler in chunk["content"]:
            assert chunk["content"].strip() != filler


def test_compress_context_noop_when_within_budget(sample_chunks):
    """target_tokens >= total tokens: chunks pass through unchanged."""
    out = compress_context(sample_chunks, target_tokens=1_000_000, use_llmlingua=False)

    for i, chunk in enumerate(out):
        assert chunk["compression_method"] == "unchanged"
        assert chunk["compressed"] is False
        assert chunk["content"] == sample_chunks[i]["content"]


def test_compress_context_empty_chunks():
    assert compress_context([], target_tokens=100, use_llmlingua=False) == []


def test_compress_context_does_not_mutate_input(sample_chunks):
    original = [dict(c) for c in sample_chunks]
    compress_context(sample_chunks, target_tokens=60, use_llmlingua=False)

    for orig, chunk in zip(original, sample_chunks, strict=True):
        assert orig["content"] == chunk["content"]


def test_compress_context_fallback_when_llmlingua_absent(sample_chunks, monkeypatch):
    """LLMLingua absent (or broken): extractive fallback still works."""
    monkeypatch.setattr("rugbrain.prompt_compression.LLMLINGUA_AVAILABLE", False)

    out = compress_context(sample_chunks, target_tokens=60, use_llmlingua=True)

    assert all(c["compression_method"] == "extractive" for c in out)
    assert ADDRESS in out[0]["content"]
    assert SECOND_ADDRESS in out[2]["content"]


def test_compress_context_handles_text_field():
    """Chunks carrying ``text`` instead of ``content`` are compressed too."""
    chunks = [{"id": "x", "text": "This is a long filler sentence with no signal at all. " * 8}]
    out = compress_context(chunks, target_tokens=20, use_llmlingua=False)

    assert out[0]["tokens_after"] < out[0]["tokens_before"]
    assert "text" in out[0] and out[0]["text"] != chunks[0]["text"]
    # content field is not introduced when the chunk used ``text``.
    assert "content" not in out[0]


def test_pack_context_orders_top_first_middle_query_last():
    """Rerank scores drive top-of-window placement; query always LAST."""
    chunks = [
        {"id": "a", "content": "low relevance", "rerank_score": 0.1},
        {"id": "b", "content": "HIGH relevance", "rerank_score": 0.95},
        {"id": "c", "content": "mid relevance", "rerank_score": 0.5},
        {"id": "d", "content": "second high", "rerank_score": 0.9},
    ]
    prompt = pack_context(chunks, "user query?", top_chunks=2)
    parts = prompt.split("\n\n")

    # Top-of-window = the two highest rerank scores, in order.
    assert parts[0] == "HIGH relevance"
    assert parts[1] == "second high"
    # Middle = remaining chunks (by score).
    assert parts[2] == "mid relevance"
    assert parts[3] == "low relevance"
    # Query LAST.
    assert parts[-1] == "user query?"


def test_layout_context_uses_retrieval_order_when_no_rerank_scores():
    """Without rerank keys, retrieval ``score`` drives ordering."""
    chunks = [
        {"id": "a", "content": "weak", "score": 0.2},
        {"id": "b", "content": "strong", "score": 0.9},
        {"id": "c", "content": "medium", "score": 0.5},
    ]
    parts = layout_context(chunks, "q", top_chunks=1)

    assert parts[0] == "strong"
    assert parts[1] == "medium"
    assert parts[2] == "weak"
    assert parts[-1] == "q"


def test_layout_context_preserves_order_when_no_scores():
    """All-zero / missing scores: stable sort keeps original retrieval order."""
    chunks = [
        {"id": "a", "content": "first"},
        {"id": "b", "content": "second"},
        {"id": "c", "content": "third"},
    ]
    parts = layout_context(chunks, "q", top_chunks=1)

    assert parts == ["first", "second", "third", "q"]


def test_pack_context_empty_chunks_returns_query():
    assert pack_context([], "just the query") == "just the query"


def test_pack_context_score_key_override():
    """``score_key`` forces a specific key when multiple scores are present."""
    chunks = [
        {"id": "a", "content": "A", "score": 0.99, "rerank_score": 0.1},
        {"id": "b", "content": "B", "score": 0.1, "rerank_score": 0.99},
    ]
    # By default rerank_score wins → B first.
    assert pack_context(chunks, "q", top_chunks=1).split("\n\n")[0] == "B"
    # Forcing score key → A first.
    assert pack_context(chunks, "q", top_chunks=1, score_key="score").split("\n\n")[0] == "A"
