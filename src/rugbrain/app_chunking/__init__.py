"""
RAG Chunking Engine - 2026 Modern Standards
============================================
Recursive character splitting with configurable strategies per content type.
Content-hash dedup via Redis. Quality scoring for ingestion filtering.

Strategies:
  - recursive: paragraph → sentence → word → character (default)
  - semantic: split on topic boundaries (embedding similarity drop)
  - sentence: preserve complete sentences
  - code: class/function boundary aware
  - fixed: simple token/character count

Crypto-aware behavior (added 2026-08):
  - EVM contract addresses / tx hashes (``0x…``), Solana pubkeys + tx
    signatures, Bitcoin addresses, IPFS CIDs and URLs are kept intact —
    a chunk boundary never splits an address or hash in half.
  - Code units (Solidity contract/function/event/error/struct, Rust
    ``fn``/``impl``, JS ``function``/``class``) and ABI JSON arrays are
    atomic: a split happens between units, never mid-function.
  - Markdown ``#``, reST underlined and AsciiDoc headings become section
    boundaries; each chunk carries the active section heading as context
    and metadata.

Top-tier techniques (added 2026-08):
  - Tree-sitter AST code chunking: when the tree-sitter grammars are
    installed (``tree-sitter-solidity`` / ``tree-sitter-rust``; Vyper via
    the published Python grammar), ``code_chunk`` splits Solidity/Rust/
    Vyper at real function/contract/struct AST nodes instead of
    brace-balancing, and each unit keeps its preceding comments/docstrings
    (see :mod:`rugbrain.tree_sitter_chunking`). The regex path remains the
    fallback when the grammars are missing.
  - Parent-child document mapping: ``split_doc_parent_child`` returns
    small search-oriented child chunks (~512 tokens, heading + metadata)
    that each reference a larger parent context window (the full section
    or a 2-4k-token slice) via ``parent_id`` — for parent retriever
    pipelines.

Standards (2026 consensus, token-targeted for embedding models):
  - Default: recursive, 512 tokens (~2048 chars), 15% overlap
  - News: recursive, 512 tokens (~2048 chars), 15% overlap
  - Scam reports: recursive fallback, 400 tokens (~1600 chars), 10% overlap
  - Contract code: code-aware, 256 tokens (~1024 chars), 20% overlap
  - Wallet profiles: fixed, 128 tokens (~512 chars), 0% overlap
  - Annual reports: recursive, 800 tokens (~3200 chars), 15% overlap

``chunk_size`` values are character targets (roughly 4 chars/token);
``max_chars`` is a per-strategy hard cap so no chunk exceeds the embedding
model's token window. Addresses/code units that individually exceed the cap
are kept whole (atomicity wins over size).

This package is a behavior-identical split of the former single-module
``app_chunking.py``; every former module-level name is re-exported here.
"""

from __future__ import annotations

import logging

from rugbrain.app_chunking._atoms import (
    _CODE_SPAN_RE,
    ABSOLUTE_MAX_CHARS,
    CHARS_PER_TOKEN,
    DEFAULT_TOKEN_TARGET,
    _protect_atoms,
    _protect_spans,
    _restore_atoms,
    _snap_pos,
    estimate_tokens,
)
from rugbrain.app_chunking._code_strategy import code_chunk
from rugbrain.app_chunking._code_units import (
    _abi_spans,
    _code_atom_spans,
    _code_unit_spans,
    _fence_spans,
    _innermost_spans,
    _match_balanced,
    _match_code_brace,
)
from rugbrain.app_chunking._dedup import content_hash, is_duplicate, mark_ingested
from rugbrain.app_chunking._documents import chunk_document, split_doc_into_chunks
from rugbrain.app_chunking._headings import _detect_headings, _sectionize
from rugbrain.app_chunking._parent_child import ParentChunk, split_doc_parent_child
from rugbrain.app_chunking._quality import chunk_batch, quality_score
from rugbrain.app_chunking._strategies import (
    CODE_SEPARATORS,
    RECURSIVE_SEPARATORS,
    STRATEGY_PRESETS,
    fixed_chunk,
    get_strategy,
    recursive_chunk,
    sentence_chunk,
)

logger = logging.getLogger("rag.chunking")

__all__ = [
    "ABSOLUTE_MAX_CHARS",
    "CHARS_PER_TOKEN",
    "CODE_SEPARATORS",
    "DEFAULT_TOKEN_TARGET",
    "RECURSIVE_SEPARATORS",
    "STRATEGY_PRESETS",
    "_CODE_SPAN_RE",
    "ParentChunk",
    "_abi_spans",
    "_code_atom_spans",
    "_code_unit_spans",
    "_detect_headings",
    "_fence_spans",
    "_innermost_spans",
    "_match_balanced",
    "_match_code_brace",
    "_protect_atoms",
    "_protect_spans",
    "_restore_atoms",
    "_sectionize",
    "_snap_pos",
    "chunk_batch",
    "chunk_document",
    "code_chunk",
    "content_hash",
    "estimate_tokens",
    "fixed_chunk",
    "get_strategy",
    "is_duplicate",
    "logger",
    "mark_ingested",
    "quality_score",
    "recursive_chunk",
    "sentence_chunk",
    "split_doc_into_chunks",
    "split_doc_parent_child",
]
