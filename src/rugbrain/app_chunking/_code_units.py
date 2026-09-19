"""Code-unit / ABI / fence detection for structure-aware chunking.

Locates atomic code regions — Solidity/Rust/JS units, markdown fenced
blocks, ABI JSON arrays and inline code spans — so a split happens between
units, never mid-function.
"""

from __future__ import annotations

import re

from rugbrain.app_chunking._atoms import _inline_code_spans

_CODE_UNIT_RE = re.compile(
    r"^\s*(?:(?:public|private|internal|external|pure|view|payable|virtual|override|"
    r"abstract|sealed|static|async|mut|pub|const|let|unsafe|extern)\s+)*"
    r"(?:contract|interface|library|function|constructor|modifier|receive|fallback|"
    r"event|error|struct|enum|class|trait|impl|def|fn)\b",
    re.MULTILINE,
)
_FENCE_RE = re.compile(r"^([`~]{3,})\s*(\S*)\s*$", re.MULTILINE)
_ABI_KEY_RE = re.compile(r'"(?:type|name|inputs|outputs|stateMutability)"')


def _match_code_brace(text: str, open_pos: int) -> int:
    """Return index of the brace closing the one opened at *open_pos*.

    Skips string literals (``"`` ``'`` backtick) and ``//`` / ``/* */``
    comments so a ``}`` inside a string never closes the unit early.
    """
    depth = 0
    in_str: str | None = None
    esc = False
    i = open_pos
    n = len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_str:
                in_str = None
        elif ch in {'"', "'", "`"}:
            in_str = ch
        elif ch == "/" and nxt == "/":  # line comment
            nl = text.find("\n", i)
            if nl == -1:
                return -1
            i = nl
        elif ch == "/" and nxt == "*":  # block comment
            end = text.find("*/", i + 2)
            if end == -1:
                return -1
            i = end + 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _match_balanced(text: str, open_pos: int, open_ch: str, close_ch: str) -> int:
    """Return index of the *close_ch* matching the *open_ch* at *open_pos*."""
    depth = 0
    in_str = False
    esc = False
    i = open_pos
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _innermost_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Keep spans that contain no other span (leaf units win over containers).

    A Solidity ``contract`` containing functions is dropped so the individual
    functions become the atomic units; a bare contract stays atomic.
    """
    result: list[tuple[int, int]] = []
    for s, e in spans:
        if any(s < s2 and e2 < e for s2, e2 in spans if (s2, e2) != (s, e)):
            continue
        result.append((s, e))
    return sorted(result)


def _code_unit_spans(code: str) -> list[tuple[int, int]]:
    """Find spans of code units (function/contract/struct/… blocks).

    Multi-line units are located with a balanced-brace scan; single-line
    declarations (``event Transfer(...);``, interface signatures) span their
    line only. Nested units are reduced to the innermost (leaf) spans.
    """
    spans: list[tuple[int, int]] = []
    for m in _CODE_UNIT_RE.finditer(code):
        line_end = code.find("\n", m.start())
        line_end = len(code) if line_end == -1 else line_end
        open_brace = code.find("{", m.start(), line_end + 1)
        if open_brace != -1:
            end = _match_code_brace(code, open_brace)
            if end != -1:
                spans.append((m.start(), end + 1))
                continue
        spans.append((m.start(), line_end))
    return _innermost_spans(spans)


def _fence_spans(text: str) -> list[tuple[int, int]]:
    """Find markdown fenced code blocks (````` ``` ```` … ```````, tildes too)."""
    spans: list[tuple[int, int]] = []
    matches = list(_FENCE_RE.finditer(text))
    i = 0
    while i < len(matches):
        m = matches[i]
        marker = m.group(1)
        j = i + 1
        while j < len(matches):
            cand = matches[j]
            if cand.group(1)[0] == marker[0] and len(cand.group(1)) >= len(marker):
                break
            j += 1
        if j < len(matches):
            spans.append((m.start(), matches[j].end()))
            i = j + 1
        else:
            i += 1
    return spans


def _abi_spans(text: str) -> list[tuple[int, int]]:
    """Find JSON arrays that look like an ABI (contain ``type``/``name``/…)."""
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\[", text):
        probe = text[m.start() : m.start() + 200]
        if not _ABI_KEY_RE.search(probe):
            continue
        end = _match_balanced(text, m.start(), "[", "]")
        if end != -1:
            spans.append((m.start(), end + 1))
    return spans


def _code_atom_spans(
    text: str,
    max_atom: int,
    units: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Combine fences, code units and ABI arrays into one atom span list.

    Small fenced blocks are atomic wholesale; oversized ones fall back to
    protecting their inner code units so a huge block is still split at
    function boundaries rather than mid-function. Inline code spans are also
    protected unless they sit inside a larger protected unit (so a function
    body containing `` `foo` `` in a comment stays atomic).

    *units* overrides the code-unit spans (``_code_unit_spans`` by default).
    ``code_chunk`` passes tree-sitter AST-derived spans here when the
    grammars are available; the regex/brace-balancing path stays the fallback.
    """
    spans: list[tuple[int, int]] = []
    for fs, fe in _fence_spans(text):
        if fe - fs <= max_atom:
            spans.append((fs, fe))
    protected_fences = list(spans)
    unit_spans = _code_unit_spans(text) if units is None else units
    units_kept = [
        u for u in unit_spans if not any(fs <= u[0] and u[1] <= fe for fs, fe in protected_fences)
    ]
    spans.extend(units_kept)
    spans.extend(a for a in _abi_spans(text) if not any(s <= a[0] and a[1] <= e for s, e in spans))
    spans.extend(
        s for s in _inline_code_spans(text) if not any(s0 < s[0] and s[1] < e0 for s0, e0 in spans)
    )
    return _innermost_spans(spans)
