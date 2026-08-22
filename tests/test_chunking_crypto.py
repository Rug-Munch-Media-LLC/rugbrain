"""Targeted tests for crypto-aware chunking in ``rugbrain.app_chunking``.

Covers the improvements added for crypto RAG content:

  * token/contract addresses, hashes, Solana pubkeys + tx signatures and
    URLs are never split across chunk boundaries;
  * code blocks (Solidity functions, fenced blocks, ABI JSON) stay atomic;
  * heading-aware sectioning carries metadata on each chunk;
  * ``chunk_size`` targets ~512 tokens with a hard ``max_chars`` cap;
  * the public API (``chunk_document`` returning ``list[str]``) is stable.

All of these are unit tests: no external services required.
"""

from __future__ import annotations

import importlib

ingest_common = importlib.import_module("rugbrain.ingest_common")
from rugbrain.app_chunking import (
    chunk_batch,
    chunk_document,
    code_chunk,
    fixed_chunk,
    recursive_chunk,
    sentence_chunk,
    split_doc_into_chunks,
)

# ── fixtures ──

EVM_ADDR = "0x" + "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"  # 42 chars
EVM_TX = "0x" + "aa" * 32  # 0x + 64 hex
SOLANA_ADDR = "8uJ8P6rVrXU9yK5zQX7Y3XxV4W6bZkZ5sTfUjM2s9yJd"  # 44 base58
SOLANA_SIG = (
    "5vDkqM5xQm4zVrT9pYcW3nLhJ8aBfG2sKdN7tUeR6xPwZoMqIyHjLvCsE1uAzX"
    "wQrNtYbVcDfGhJkLm"  # 88 base58 chars
)
BTC_ADDR = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
IPFS_CID = "QmYwAPJzv5CZsnAzt8auVZRnV3hNqK8tN5sVn8B8bq4m5x"

LONG_URL = "https://etherscan.io/token/0xa1b2c3d4e5f60718293a4b5c6d7e8f9012345678#readContract"

SOLIDITY = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract RugVault {
    address public owner;
    mapping(address => uint256) public balances;

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }

    function withdraw(uint256 amount) external {
        require(amount <= balances[msg.sender], "insufficient");
        balances[msg.sender] -= amount;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
    }

    function setOwner(address newOwner) external {
        owner = newOwner;
    }
}
"""


def _doc_with(atom: str, n: int = 8) -> str:
    """A long prose doc where *atom* sits at (or near) many boundaries."""
    para = (
        f"The project deployed at {atom} raised funds but shows honeypot signals: "
        "the owner can drain liquidity and mint unlimited supply."
    )
    return "\n\n".join([para] * n)


def _assert_atom_intact(chunks: list[str], atom: str) -> None:
    """The atom must appear whole in ≥1 chunk and never as a partial fragment."""
    assert any(atom in c for c in chunks), f"atom {atom!r} missing from chunks"
    mid = len(atom) // 2
    assert not any(atom[:mid] in c and atom not in c for c in chunks), (
        f"atom {atom!r} was split (head fragment) across a chunk boundary"
    )
    assert not any(atom[-mid:] in c and atom not in c for c in chunks), (
        f"atom {atom!r} was split (tail fragment) across a chunk boundary"
    )


# ── 1. address / hash / URL preservation ──


def test_recursive_chunk_keeps_evm_address_intact() -> None:
    chunks = recursive_chunk(_doc_with(EVM_ADDR), chunk_size=180, overlap=0.15)
    _assert_atom_intact(chunks, EVM_ADDR)


def test_recursive_chunk_keeps_evm_tx_hash_intact() -> None:
    chunks = recursive_chunk(_doc_with(EVM_TX), chunk_size=160, overlap=0.1)
    _assert_atom_intact(chunks, EVM_TX)


def test_recursive_chunk_keeps_solana_address_intact() -> None:
    chunks = recursive_chunk(_doc_with(SOLANA_ADDR), chunk_size=180, overlap=0.15)
    _assert_atom_intact(chunks, SOLANA_ADDR)


def test_recursive_chunk_keeps_solana_signature_intact() -> None:
    chunks = recursive_chunk(_doc_with(SOLANA_SIG), chunk_size=200, overlap=0.15)
    _assert_atom_intact(chunks, SOLANA_SIG)


def test_recursive_chunk_keeps_url_intact() -> None:
    chunks = recursive_chunk(_doc_with(LONG_URL), chunk_size=200, overlap=0.15)
    _assert_atom_intact(chunks, LONG_URL)


def test_recursive_chunk_keeps_btc_address_intact() -> None:
    chunks = recursive_chunk(_doc_with(BTC_ADDR), chunk_size=170, overlap=0.15)
    _assert_atom_intact(chunks, BTC_ADDR)


def test_recursive_chunk_keeps_ipfs_cid_intact() -> None:
    chunks = recursive_chunk(_doc_with(IPFS_CID), chunk_size=150, overlap=0.15)
    _assert_atom_intact(chunks, IPFS_CID)


def test_fixed_chunk_keeps_address_intact() -> None:
    chunks = fixed_chunk(_doc_with(EVM_ADDR), chunk_size=150, overlap=0.0)
    _assert_atom_intact(chunks, EVM_ADDR)


def test_sentence_chunk_keeps_url_intact() -> None:
    # URLs contain '.' which the sentence splitter would otherwise cut on.
    text = (
        f"Review the explorer page at {LONG_URL}. Then check the deployer wallet. "
        "The token shows red flags. Inspect the contract source next. "
        "Finally compare holders. Do not buy yet. Wait for the audit. "
        "The team is anonymous. Liquidity is locked. Supply is owned. "
    )
    chunks = sentence_chunk(text, chunk_size=120, overlap=0.15)
    _assert_atom_intact(chunks, LONG_URL)


def test_ingest_common_chunk_text_keeps_address_intact() -> None:
    chunks = ingest_common.chunk_text(_doc_with(EVM_ADDR), size=200, overlap=50)
    _assert_atom_intact(chunks, EVM_ADDR)


# ── 2. code-block / ABI atomicity ──


def test_code_chunk_keeps_function_bodies_atomic() -> None:
    chunks = code_chunk(SOLIDITY, chunk_size=120, overlap=0.2)
    for func in (
        "function deposit() external payable {",
        "function withdraw(uint256 amount) external {",
        "function setOwner(address newOwner) external {",
    ):
        assert any(func in c for c in chunks), f"{func} missing"
        assert not any(func[: len(func) // 2] in c and func not in c for c in chunks)


def test_code_chunk_keeps_abi_json_atomic() -> None:
    abi = (
        '[{"type":"function","name":"withdraw","inputs":['
        '{"name":"amount","type":"uint256"}],"outputs":[],'
        '"stateMutability":"nonpayable"},'
        '{"type":"event","name":"Transfer","inputs":['
        '{"name":"from","type":"address"},{"name":"to","type":"address"}],"anonymous":false}]'
    )
    doc = f"ABI for the token:\n{abi}\n\nThis interface lets anyone withdraw."
    chunks = code_chunk(doc, chunk_size=140, overlap=0.2)
    # No chunk may contain a half-open ABI fragment (partial JSON element).
    assert any("withdraw" in c and '"type"' in c for c in chunks)
    for c in chunks:
        assert c.count("{") == c.count("}"), "ABI braces unbalanced inside a chunk"


def test_recursive_chunk_keeps_fenced_code_block_together() -> None:
    block = (
        "```solidity\nfunction rescue() external { owner.transfer(address(this).balance); }\n```"
    )
    text = (
        "The critical function lives here:\n"
        + block
        + "\n\nIt lets the owner pull all ETH from the vault contract."
    ) * 4
    chunks = recursive_chunk(text, chunk_size=150, overlap=0.15)
    assert any(block in c for c in chunks), "fenced block was split"


def test_code_chunk_keeps_single_line_signatures() -> None:
    src = (
        "pragma solidity ^0.8.20;\n"
        "interface IERC20 {\n"
        "    function transfer(address to, uint256 amount) external returns (bool);\n"
        "    function approve(address spender, uint256 amount) external returns (bool);\n"
        "}\n"
    )
    chunks = code_chunk(src, chunk_size=90, overlap=0.2)
    for sig in (
        "function transfer(address to, uint256 amount) external returns (bool);",
        "function approve(address spender, uint256 amount) external returns (bool);",
    ):
        assert any(sig in c for c in chunks), f"{sig} missing"
        assert not any(sig[: len(sig) // 2] in c and sig not in c for c in chunks)


# ── 3. heading-aware sectioning + metadata ──


def test_split_doc_into_chunks_carries_section_metadata() -> None:
    doc = (
        "# Audit Findings\n\n"
        f"The contract at {EVM_ADDR} has a critical vulnerability in withdraw.\n\n"
        "## Risk Assessment\n\n"
        "Medium risk. Owner can mint tokens.\n\n"
        "## Conclusion\n\n"
        "Do not approve tokens."
    )
    chunks = split_doc_into_chunks(doc, "scam_report", doc_meta={"source": "auditor"})
    assert chunks, "expected chunks"
    sections = [c["section"] for c in chunks]
    assert "Audit Findings" in sections
    assert "Risk Assessment" in sections
    assert all(c["doc_type"] == "scam_report" for c in chunks)
    assert all(c["total_chunks"] == len(chunks) for c in chunks)
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    # heading is used as embedding context inside the chunk text
    assert chunks[0]["text"].startswith("# Audit Findings")
    assert all(c["estimated_tokens"] >= 1 for c in chunks)


def test_split_doc_into_chunks_rest_heading() -> None:
    doc = (
        "Audit Findings\n"
        "==============\n\n"
        "The audit found 3 high severity issues.\n\n"
        "Recommendations\n"
        "---------------\n\n"
        "Disable the mint function immediately."
    )
    chunks = split_doc_into_chunks(doc, "annual_report")
    labels = [c["section"] for c in chunks]
    assert "Audit Findings" in labels
    assert "Recommendations" in labels


def test_heading_sections_are_not_mixed_in_one_chunk() -> None:
    doc = (
        "# Section One\n\n"
        + ("alpha content " * 60)
        + "\n\n# Section Two\n\n"
        + ("beta content " * 60)
    )
    chunks = split_doc_into_chunks(doc, "default")
    for c in chunks:
        if "# Section One" in c["text"]:
            assert "beta" not in c["text"], "sections leaked into each other"
        if "# Section Two" in c["text"]:
            assert "alpha" not in c["text"], "sections leaked into each other"


# ── 4. token target + hard cap ──


def test_presets_target_tokens_and_cap() -> None:
    from rugbrain.app_chunking import STRATEGY_PRESETS

    for name, cfg in STRATEGY_PRESETS.items():
        assert cfg["chunk_size"] >= 4 * 128, f"{name}: target below ~128 tokens"
        assert cfg["max_chars"] >= cfg["chunk_size"], f"{name}: cap below target"
        assert cfg["max_chars"] <= 8000, f"{name}: cap above embedder truncation"


def test_recursive_chunk_hard_cap_respected() -> None:
    text = "long boring filler text " * 400  # ~8800 chars, no crypto atoms
    chunks = recursive_chunk(text, chunk_size=512, overlap=0.15, max_chars=640)
    assert all(len(c) <= 640 for c in chunks)


def test_chunk_document_returns_strings_and_is_stable() -> None:
    out = chunk_document(_doc_with(EVM_ADDR, n=6), "scam_report")
    assert isinstance(out, list)
    assert all(isinstance(c, str) for c in out)
    assert any(EVM_ADDR in c for c in out)


def test_chunk_batch_adds_section_metadata() -> None:
    docs = [
        {
            "text": f"# Update\n\nRug detected at {EVM_ADDR}.\n\n# Details\n\nLP removed.",
            "source": "tokensniffer",
            "url": "https://example.com/x",
            "category": "alert",
        }
    ]
    chunks = chunk_batch(docs, "news", min_quality=1)
    assert chunks
    first = chunks[0]
    for key in (
        "text",
        "chunk_index",
        "total_chunks",
        "quality_score",
        "content_hash",
        "section",
        "doc_type",
        "estimated_tokens",
        "source",
        "url",
    ):
        assert key in first, f"missing {key} in chunk dict"
    assert any(EVM_ADDR in c["text"] for c in chunks)
