"""Structure-aware code chunking (``code_chunk``).

Functions/contracts/events/structs (and ABI JSON arrays / fenced code
blocks) are kept atomic: splits happen between logical units, never
mid-function. Falls back to plain recursive splitting on prose.
"""

from __future__ import annotations

from rugbrain.app_chunking._atoms import _protect_spans, _restore_atoms
from rugbrain.app_chunking._code_units import _code_atom_spans, _code_unit_spans
from rugbrain.app_chunking._strategies import CODE_SEPARATORS, recursive_chunk
from rugbrain.tree_sitter_chunking import ts_code_unit_spans


def code_chunk(
    code: str,
    chunk_size: int = 256,
    overlap: float = 0.20,
    max_chars: int | None = None,
) -> list[str]:
    """Structure-aware chunking for smart contract code.

    Functions/contracts/events/structs (and ABI JSON arrays / fenced code
    blocks) are kept atomic: splits happen between logical units, never
    mid-function. Falls back to plain recursive splitting on prose.

    Upgraded 2026-08: when the tree-sitter grammars are installed the code
    units come from a real AST (see :mod:`rugbrain.tree_sitter_chunking`),
    so boundaries land on function/contract/struct nodes — each unit keeps
    its preceding comments/docstrings. Without tree-sitter the regex +
    brace-balancing path is used unchanged.
    """
    if not code or len(code) < 50:
        return [code] if code else []
    max_chars = max_chars or chunk_size
    # Tree-sitter AST-aware units when available; the regex brace-balancing
    # path remains the fallback (also used for prose/unknown languages).
    units = ts_code_unit_spans(code)
    if units is None:
        units = _code_unit_spans(code)
    protected, atoms = _protect_spans(
        code, _code_atom_spans(code, max_atom=max_chars, units=units), prefix="C"
    )
    chunks = recursive_chunk(protected, chunk_size, overlap, CODE_SEPARATORS, max_chars)
    return [_restore_atoms(c, atoms) for c in chunks]
