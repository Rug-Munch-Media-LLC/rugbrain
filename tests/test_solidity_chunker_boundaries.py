"""Behaviour tests for AST-aware Solidity chunking (ingest/chunk path).

The chunker must never mangle a contract unit and must always degrade to the
regex path when solc cannot parse the source.
"""

from rugbrain.solidity_chunker import (
    MIN_CHUNK_SIZE,
    _build_chunks_from_boundaries,
    _chunk_solidity_regex,
    _compute_line_offsets,
    chunk_solidity_ast,
    parse_solidity_ast,
)

UNPARSEABLE = "this is not solidity {{{\ncontract\n"

SOURCE = """pragma solidity ^0.8.0;
import "./IERC20.sol";

contract Vault {
    function deposit(uint256 amount) external {
        require(amount > 0, "zero");
    }
}
"""


def test_line_offsets_map_each_line_start():
    assert _compute_line_offsets("ab\ncd\n") == [0, 3, 6]


def test_line_offsets_of_empty_source_is_origin():
    assert _compute_line_offsets("") == [0]


def test_build_chunks_splits_when_max_size_exceeded():
    source = "a" * 400 * 2
    boundaries = [(0, 400, "contract"), (400, 800, "function")]

    chunks = _build_chunks_from_boundaries(source, boundaries, target_size=500, max_size=500)

    assert len(chunks) == 2
    assert all(len(chunk) >= MIN_CHUNK_SIZE for chunk in chunks)


def test_build_chunks_drops_segments_below_minimum_size():
    assert _build_chunks_from_boundaries("tiny", [(0, 4, "contract")], 2000, 3500) == []


def test_parse_solidity_ast_returns_none_for_invalid_source():
    assert parse_solidity_ast(UNPARSEABLE) is None


def test_chunk_solidity_ast_falls_back_to_regex_on_parse_failure():
    chunks = chunk_solidity_ast(UNPARSEABLE)

    assert chunks
    assert all(isinstance(chunk, str) for chunk in chunks)


def test_regex_chunker_keeps_every_function_body():
    joined = "\n".join(_chunk_solidity_regex(SOURCE, target_size=2000, max_size=3500))

    assert "function deposit" in joined
    assert 'require(amount > 0, "zero");' in joined
    assert 'import "./IERC20.sol"' in joined


def test_regex_chunker_returns_nothing_for_whitespace_only_source():
    assert _chunk_solidity_regex("   \n\n  ") == []
