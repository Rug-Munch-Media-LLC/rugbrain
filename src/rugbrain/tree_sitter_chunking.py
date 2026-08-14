"""
Tree-sitter AST code chunking (Solidity / Rust / Vyper).
=========================================================
Upgrades the regex + brace-balancing code-unit detection in
``rugbrain.app_chunking.code_chunk`` with real syntax trees: units are
split at function/contract/struct boundaries via the AST, and each unit
stays atomic together with its preceding comments/docstrings.

Grammar availability
--------------------
  - ``tree-sitter-solidity`` — Solidity: contract/interface/library,
    function/constructor, struct/enum/event/error/modifier and
    state-variable declarations.
  - ``tree-sitter-rust`` — Rust: fn/impl/struct/enum/trait/mod, const,
    static, type and use declarations.
  - ``tree-sitter-python`` — used as the closest published grammar for
    **Vyper**. There is no official ``tree-sitter-vyper`` on PyPI as of
    2026-08 (vendored-grammar note): Vyper functions carrying
    ``@external`` / ``@payable`` / ``@view`` decorators parse as Python
    ``decorated_definition`` nodes, which is enough for function-atomic
    chunking. Vyper ``struct`` definitions are not reliably parsed and
    fall back to prose fill, same as the pre-tree-sitter path.

All grammars are loaded lazily via :mod:`importlib`, so importing this
module is safe when the tree-sitter packages are not installed. When a
grammar is unavailable, :func:`ts_code_unit_spans` returns ``None`` and
callers fall back to the existing regex path.

Public API
----------
  - :func:`tree_sitter_available` — is the core binding importable?
  - :func:`language_available` — is the grammar for *language* importable?
  - :func:`detect_language` — heuristic language sniffing.
  - :func:`ts_code_unit_spans` — AST-derived atomic unit spans, or ``None``
    when the caller should fall back to regex-based chunking.
"""

from __future__ import annotations

import importlib
import logging
import re
import warnings
from typing import Any

logger = logging.getLogger("rag.chunking.treesitter")

# Grammar module per logical language. "vyper" deliberately reuses the
# published Python grammar — the closest available approximation.
_GRAMMAR_MODULE: dict[str, str] = {
    "solidity": "tree_sitter_solidity",
    "rust": "tree_sitter_rust",
    "vyper": "tree_sitter_python",
    "python": "tree_sitter_python",
}

# AST node types that form atomic units per language. Containers (contract,
# impl, class, …) are still descended into, so nested functions win under
# the innermost-span reduction — the same semantics as the regex path.
_BOUNDARY_TYPES: dict[str, frozenset[str]] = {
    "solidity": frozenset(
        {
            "contract_declaration",
            "interface_declaration",
            "library_declaration",
            "function_definition",  # constructors are function_definition in this grammar
            "constructor_definition",
            "struct_declaration",
            "enum_declaration",
            "event_definition",
            "error_declaration",
            "modifier_definition",
            "state_variable_declaration",
            "pragma_directive",
            "import_directive",
        }
    ),
    "rust": frozenset(
        {
            "function_item",
            "macro_definition",
            "struct_item",
            "enum_item",
            "trait_item",
            "impl_item",
            "mod_item",
            "type_item",
            "const_item",
            "static_item",
            "use_declaration",
        }
    ),
    "vyper": frozenset(
        {
            "decorated_definition",
            "function_definition",
            "class_definition",
            "import_statement",
            "import_from_statement",
        }
    ),
    "python": frozenset(
        {
            "decorated_definition",
            "function_definition",
            "class_definition",
            "import_statement",
            "import_from_statement",
        }
    ),
}

# Node types whose children must NOT be collected: the decorators of a
# Python/Vyper ``decorated_definition`` belong to the same unit, so carving
# the inner ``function_definition`` off would lose ``@external`` etc.
_NO_DESCEND: dict[str, frozenset[str]] = {
    "vyper": frozenset({"decorated_definition"}),
    "python": frozenset({"decorated_definition"}),
}

# Lines that count as attached documentation directly above a unit.
_COMMENT_MARKERS: dict[str, tuple[str, ...]] = {
    "solidity": ("//", "/*", "*", "*/"),
    "rust": ("//", "/*", "*", "*/"),
    "vyper": ("#", '"""', "'''"),
    "python": ("#", '"""', "'''"),
}

# ── Language sniffing (scoring; strong signals win) ──

_SOLIDITY_SIGNALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bpragma\s+solidity\b"), 12),
    (re.compile(r"\b(?:contract|interface|library)\s+\w+\s*\{"), 10),
    (re.compile(r"SPDX-License-Identifier"), 5),
    (re.compile(r"\bmapping\s*\("), 4),
    (re.compile(r"\bmsg\.sender\b"), 4),
    (re.compile(r"\brequire\s*\("), 3),
    (re.compile(r"\baddress\s+payable\b"), 3),
    (re.compile(r"\b(?:function|modifier|event|error)\s+\w+\s*(?:\(|\{)"), 2),
)

_RUST_SIGNALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bfn\s+\w+\s*\("), 5),
    (re.compile(r"\bimpl\s+\w+"), 5),
    (re.compile(r"\buse\s+[A-Za-z_:]+\s*::"), 5),
    (re.compile(r"\bpub\s+(?:struct|enum|trait|fn|mod|const|static|use|type)\b"), 5),
    (re.compile(r"\blet\s+mut\b"), 3),
    (re.compile(r"->\s*Result\s*<"), 3),
    (re.compile(r"#\[derive\b"), 3),
    (re.compile(r"\bmod\s+\w+\s*(?:\{|;)"), 3),
)

_VYPER_SIGNALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (
        re.compile(r"@(?:external|payable|view|pure|constant|nonreentrant|internal|deploy)\b"),
        10,
    ),
    (re.compile(r"\bmsg\.(?:sender|value)\b"), 6),
    (re.compile(r"\bblock\.(?:timestamp|number|coinbase)\b"), 5),
    (re.compile(r"\bself\.balance\b"), 5),
    (re.compile(r"\bstruct\s+\w+\s*:"), 5),
    (
        re.compile(
            r"\bdef\s+\w+\([^)]*:\s*(?:uint256|uint128|int128|int256|address|bool|"
            r"bytes(?:32)?|String)\b"
        ),
        4,
    ),
)

_PYTHON_SIGNALS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^(?:from\s+\S+\s+import|import\s+\S+)", re.MULTILINE), 4),
    (re.compile(r"^class\s+\w+"), 4),
    (re.compile(r"\bif\s+__name__\s*==\s*[\"']__main__"), 4),
    (re.compile(r"^def\s+\w+\s*\("), 3),
    (re.compile(r"\bprint\s*\("), 2),
)


def _score(signals: tuple[tuple[re.Pattern[str], int], ...], text: str) -> int:
    return sum(weight for pattern, weight in signals if pattern.search(text))


# ── Lazy core binding + per-grammar parser cache ──

_tree_sitter: Any | None = None
_tree_sitter_probed = False
# Cached per grammar-module; None means "tried and unavailable".
_parsers: dict[str, Any | None] = {}


def _load(module: str) -> Any | None:
    try:
        return importlib.import_module(module)
    except ImportError:
        return None
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("import %s failed: %s", module, e)
        return None


def _tree_sitter_core() -> Any | None:
    global _tree_sitter, _tree_sitter_probed
    if not _tree_sitter_probed:
        _tree_sitter_probed = True
        _tree_sitter = _load("tree_sitter")
    return _tree_sitter


def _parser_for(language: str) -> Any | None:
    module = _GRAMMAR_MODULE.get(language)
    if module is None:
        return None
    if module in _parsers:
        return _parsers[module]
    core = _tree_sitter_core()
    if core is None:
        _parsers[module] = None
        return None
    try:
        grammar = importlib.import_module(module)
        # tree_sitter_solidity only exposes the legacy int-pointer form of
        # ``language()`` (deprecated upstream, still the only public API);
        # suppress the warning so chunking logs/tests stay clean.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            lang = core.Language(grammar.language())
        parser = core.Parser(lang)
    except Exception as e:
        logger.debug("tree-sitter %s init failed: %s", module, e)
        _parsers[module] = None
        return None
    _parsers[module] = parser
    return parser


# ── Public API ──


def tree_sitter_available() -> bool:
    """Return True when the tree-sitter core binding is importable."""
    return _tree_sitter_core() is not None


def language_available(language: str) -> bool:
    """Return True when the grammar for *language* is importable.

    ``language`` is one of ``solidity``, ``rust``, ``vyper`` or ``python``.
    """
    module = _GRAMMAR_MODULE.get(language)
    return module is not None and _load(module) is not None


def detect_language(code: str) -> str | None:
    """Sniff the dominant language of *code*.

    Returns ``solidity`` / ``rust`` / ``vyper`` / ``python``, or ``None``
    when no language is confidently detected (prose, JSON, unknown). Only
    the head of the input is scanned — enough signal for chunking.
    """
    if not code:
        return None
    text = code[:20000]
    scores: dict[str, int] = {
        "solidity": _score(_SOLIDITY_SIGNALS, text),
        "rust": _score(_RUST_SIGNALS, text),
        "vyper": _score(_VYPER_SIGNALS, text),
        "python": _score(_PYTHON_SIGNALS, text),
    }
    # dict iteration order gives a deterministic tie-break (solidity first).
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


def ts_code_unit_spans(code: str, language: str | None = None) -> list[tuple[int, int]] | None:
    """Return AST-derived atomic unit spans for *code*.

    Each span ``(start, end)`` covers a complete logical unit — function,
    contract, struct, enum, event, modifier, … — extended backward over any
    directly-attached comments/docstrings. Spans are nested-reduced to the
    innermost units (functions win over their containing contract).

    Returns ``None`` when tree-sitter is unavailable, the language is not
    detected, or the parse is too error-heavy to trust — the caller should
    fall back to the regex/brace-balancing path. Returns ``[]`` for empty
    input or code with no boundary nodes.
    """
    if not code or not code.strip():
        return []
    lang = language or detect_language(code)
    if lang is None:
        return None
    parser = _parser_for(lang)
    if parser is None:
        return None
    try:
        tree = parser.parse(code.encode("utf-8"))
    except Exception as e:
        logger.debug("tree-sitter parse failed for %s: %s", lang, e)
        return None

    root = tree.root_node
    spans = _collect_boundaries(root, _BOUNDARY_TYPES[lang], _NO_DESCEND.get(lang, frozenset()))
    if not spans and root.has_error:
        # The parse was mostly ERROR nodes (e.g. prose sniffed as code):
        # defer to the regex path rather than emit garbage spans.
        return None

    attached = [(_attach_preceding_docs(code, s, lang), e) for s, e in spans if e > s]
    return _innermost_spans(attached)


# ── AST walking ──


def _collect_boundaries(
    root: Any,
    boundary: frozenset[str],
    no_descend: frozenset[str],
) -> list[tuple[int, int]]:
    """Collect ``(start_byte, end_byte)`` for every boundary-type node."""
    spans: list[tuple[int, int]] = []
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        if node.type in boundary:
            spans.append((node.start_byte, node.end_byte))
            if node.type in no_descend:
                continue
        for child in reversed(node.named_children):
            stack.append(child)
    return spans


def _attach_preceding_docs(text: str, start: int, language: str) -> int:
    """Extend *start* backward to include the comment block above the unit.

    Attaches contiguous comment lines (plus at most one blank separator),
    so a unit keeps its NatSpec/Rustdoc/docstring context. Stops at the
    first non-comment, non-blank line.
    """
    markers = _COMMENT_MARKERS.get(language, ())
    if not markers:
        return start
    pos = start
    blanks = 0
    while pos > 0:
        if text[pos - 1] == "\n":
            pos -= 1  # consume the newline ending the previous line
        prev_nl = text.rfind("\n", 0, pos)
        if prev_nl == -1:
            line = text[:pos]
            line_start = 0
        else:
            line = text[prev_nl + 1 : pos]
            line_start = prev_nl + 1
        stripped = line.strip()
        if not stripped:
            blanks += 1
            if blanks > 1:
                break
            pos = line_start
            continue
        if any(stripped.startswith(marker) for marker in markers):
            pos = line_start
            blanks = 0
            continue
        break
    return pos


def _innermost_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Keep spans that contain no other span (leaf units win over containers)."""
    result: list[tuple[int, int]] = []
    for s, e in spans:
        if any(s < s2 and e2 < e for s2, e2 in spans if (s2, e2) != (s, e)):
            continue
        result.append((s, e))
    return sorted(result)
