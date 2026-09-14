"""Tree-sitter AST function-atomic chunking tests.

Covers the tree-sitter upgrade in ``rugbrain.app_chunking.code_chunk``:
AST-derived boundaries keep whole functions/contracts/structs atomic
together with their preceding comments, for Solidity / Rust / Vyper.

The tree-sitter grammars are optional; every test in this module is
skipped when they are not installed (the module still imports cleanly —
``rugbrain.app_chunking`` falls back to the regex path in that case).

All tests are unit tests: no external services required.
"""

from __future__ import annotations

import pytest

from rugbrain.app_chunking import code_chunk
from rugbrain.tree_sitter_chunking import detect_language, ts_code_unit_spans


@pytest.fixture(scope="module", autouse=True)
def _treesitter_required() -> None:
    """Skip the module's tests when the tree-sitter grammars are missing."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_solidity")
    pytest.importorskip("tree_sitter_rust")


SOLIDITY = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract RugVault {
    address public owner;

    /// @notice deposit funds into the vault
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

RUST = """//! wallet module
use std::collections::HashMap;

/// A wallet.
pub struct Wallet {
    pub balance: u64,
}

impl Wallet {
    pub fn new(balance: u64) -> Self {
        Self { balance }
    }

    fn deposit(&mut self, amount: u64) {
        self.balance += amount;
    }
}

pub fn main() -> Result<(), String> {
    let mut w = Wallet::new(10);
    w.deposit(5);
    Ok(())
}
"""

VYPER = """# @version ^0.3.10
owner: public(address)

@external
@payable
def deposit() -> None:
    self.balance += msg.value

@external
def withdraw(amount: uint256) -> bool:
    assert amount <= self.balance, "insufficient"
    self.balance -= amount
    return True
"""


def _assert_unit_atomic(chunks: list[str], unit: str) -> None:
    """*unit* must appear whole in ≥1 chunk and never as a partial fragment."""
    assert any(unit in c for c in chunks), f"{unit!r} missing from chunks"
    mid = len(unit) // 2
    assert not any(unit[:mid] in c and unit not in c for c in chunks), (
        f"{unit!r} was split across a chunk boundary"
    )


# ── language sniffing ──


def test_detect_language_solidity() -> None:
    assert detect_language(SOLIDITY) == "solidity"


def test_detect_language_rust() -> None:
    assert detect_language(RUST) == "rust"


def test_detect_language_vyper() -> None:
    assert detect_language(VYPER) == "vyper"


def test_detect_language_none_for_prose() -> None:
    assert detect_language("The rug was pulled with 5000 holders watching closely.") is None


# ── AST unit extraction ──


def test_ts_units_found_for_solidity() -> None:
    spans = ts_code_unit_spans(SOLIDITY)
    assert spans
    # the contract is a container: the three functions should be leaf atoms
    for body in (SOLIDITY[s:e] for s, e in spans):
        assert body.count("{") == body.count("}"), "unit braces unbalanced"
    joined = "".join(SOLIDITY[s:e] for s, e in spans)
    assert "function deposit() external payable" in joined
    assert "function withdraw(uint256 amount) external" in joined
    assert "function setOwner(address newOwner) external" in joined


def test_comment_attached_to_function() -> None:
    spans = ts_code_unit_spans(SOLIDITY)
    assert spans
    deposit = next(SOLIDITY[s:e] for s, e in spans if "function deposit" in SOLIDITY[s:e])
    assert "/// @notice deposit funds into the vault" in deposit


def test_bare_contract_without_functions_stays_atomic() -> None:
    src = "// SPDX-License-Identifier: MIT\ncontract Storage {\n    uint256 public value;\n}\n"
    chunks = code_chunk(src, chunk_size=64, overlap=0.2)
    _assert_unit_atomic(chunks, "contract Storage {")


# ── code_chunk atomicity per language ──


def test_solidity_functions_atomic() -> None:
    chunks = code_chunk(SOLIDITY, chunk_size=120, overlap=0.2)
    for func in (
        "function deposit() external payable {",
        "function withdraw(uint256 amount) external {",
        "function setOwner(address newOwner) external {",
    ):
        _assert_unit_atomic(chunks, func)


def test_rust_functions_and_struct_atomic() -> None:
    pytest.importorskip("tree_sitter_rust")
    chunks = code_chunk(RUST, chunk_size=90, overlap=0.2)
    for unit in (
        "pub fn new(balance: u64)",
        "fn deposit(&mut self, amount: u64)",
        "pub fn main()",
        "pub struct Wallet {",
    ):
        _assert_unit_atomic(chunks, unit)


def test_vyper_decorated_functions_atomic() -> None:
    pytest.importorskip("tree_sitter_python")
    chunks = code_chunk(VYPER, chunk_size=80, overlap=0.2)
    for unit in (
        "@external\n@payable\ndef deposit() -> None:",
        "@external\ndef withdraw(amount: uint256) -> bool:",
    ):
        _assert_unit_atomic(chunks, unit)


def test_prose_falls_back_to_recursive() -> None:
    text = "The rug was pulled at 0x" + "a1" * 20 + " with 5000 holders watching. " * 20
    chunks = code_chunk(text, chunk_size=120, overlap=0.15)
    assert chunks
    assert any("0x" in c for c in chunks)
