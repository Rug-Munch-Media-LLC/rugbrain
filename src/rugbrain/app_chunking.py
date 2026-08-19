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
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from rugbrain.tree_sitter_chunking import ts_code_unit_spans

logger = logging.getLogger("rag.chunking")

# ── Token budget ──
CHARS_PER_TOKEN = 4  # conservative proxy (~4 chars/token)
DEFAULT_TOKEN_TARGET = 512  # embedding-model sweet spot (tokens)
ABSOLUTE_MAX_CHARS = 8000  # embedder truncation guard (see embed paths)

# ── Strategy presets per content type ──
# chunk_size is a character target (~4 chars/token); max_chars is the hard
# cap for that strategy (never exceeded except for indivisible atoms).
STRATEGY_PRESETS = {
    "news": {"strategy": "recursive", "chunk_size": 2048, "overlap": 0.15, "max_chars": 2560},
    "scam_report": {"strategy": "semantic", "chunk_size": 1600, "overlap": 0.10, "max_chars": 2000},
    "contract_code": {"strategy": "code", "chunk_size": 1024, "overlap": 0.20, "max_chars": 1536},
    "wallet_profile": {"strategy": "fixed", "chunk_size": 512, "overlap": 0.0, "max_chars": 768},
    "annual_report": {
        "strategy": "recursive",
        "chunk_size": 3200,
        "overlap": 0.15,
        "max_chars": 4000,
    },
    "social_post": {"strategy": "fixed", "chunk_size": 1024, "overlap": 0.05, "max_chars": 1280},
    "market_data": {"strategy": "semantic", "chunk_size": 1200, "overlap": 0.10, "max_chars": 1600},
    "default": {"strategy": "recursive", "chunk_size": 2048, "overlap": 0.15, "max_chars": 2560},
}

# ── Separator hierarchy for recursive splitting ──
RECURSIVE_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
CODE_SEPARATORS = ["\n\nclass ", "\n\ndef ", "\n\n", "\n", " ", ""]


# ── Crypto atom protection ──
# Spans matching these patterns are swapped out for tiny placeholders before
# splitting and restored afterwards, so a boundary never lands mid-address.
# Two placeholder namespaces exist so nested protection passes never collide:
# ``\x00K<idx>\x00`` (prose atoms from ``_protect_atoms``) and ``\x00KC<idx>\x00``
# (code-unit atoms from ``code_chunk``'s pre-protection pass).
_PH_RE = re.compile(r"\x00K(?:C)?\d+\x00")

_ATOM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # URLs first: they can contain 0x/base58 runs in query strings.
    re.compile(r"https?://[^\s<>()\"']{12,}"),
    # EVM addresses (0x + 40) and hashes (0x + 64); greedy covers longer hex runs.
    re.compile(r"\b0x[a-fA-F0-9]{40,}"),
    # Solana transaction signatures (87-88 base58 chars).
    re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{87,88}\b"),
    # Bitcoin addresses (bech32 / legacy P2PKH-P2SH).
    re.compile(r"\b(?:bc1[ac-hj-np-z02-9]{8,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b"),
    # IPFS CIDs (v0 Qm…, v1 bafy…).
    re.compile(r"\b(?:Qm[1-9A-HJ-NP-Za-km-z]{44}|bafy[a-zA-Z0-9]{46,59})\b"),
    # Solana pubkeys / program IDs (32-44 base58 chars).
    re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"),
)


def _protect_atoms(text: str) -> tuple[str, dict[str, str]]:
    """Replace crypto atoms (addresses/hashes/URLs) with placeholders.

    Returns ``(protected_text, atoms)`` where ``atoms`` maps each placeholder
    back to the original span. Call :func:`_restore_atoms` after splitting.

    Inline code spans (`` `code` ``) are protected first so a span that
    contains an address is kept whole (backticks included) and a chunk
    boundary never splits mid-span.
    """
    spans: list[tuple[int, int]] = _inline_code_spans(text)
    for pat in _ATOM_PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < e2 and e > s2 for s2, e2 in spans):
                continue  # already covered by a wider inline code span
            spans.append((s, e))
    return _protect_spans(text, spans)


def _protect_spans(
    text: str, spans: list[tuple[int, int]], prefix: str = ""
) -> tuple[str, dict[str, str]]:
    """Replace *spans* (non-overlapping) with ``\\x00K<prefix><idx>\\x00`` placeholders.

    *prefix* (e.g. ``"C"`` for code units) separates namespaces so a later
    ``_protect_atoms`` pass inside :func:`recursive_chunk` never reuses a
    placeholder id already taken by this pass.
    """
    out: list[str] = []
    atoms: dict[str, str] = {}
    prev = 0
    for i, (s, e) in enumerate(sorted(spans)):
        s = max(s, prev)  # defensive: never overlap already-protected region
        if s >= e or s >= len(text):
            continue
        out.append(text[prev:s])
        ph = f"\x00K{prefix}{i}\x00"
        atoms[ph] = text[s:e]
        out.append(ph)
        prev = e
    out.append(text[prev:])
    return "".join(out), atoms


def _restore_atoms(text: str, atoms: dict[str, str]) -> str:
    """Put protected spans back into *text* after splitting."""
    if not atoms:
        return text
    for ph, orig in atoms.items():
        text = text.replace(ph, orig)
    return text


def _snap_pos(text: str, pos: int) -> int:
    """Move *pos* right so it never sits inside a protected placeholder.

    A chunk boundary that would cut an address/hash/code-unit in half is
    pushed just past the end of that placeholder instead.
    """
    pos = max(0, pos)
    lo = max(0, pos - 16)  # placeholder is ≤ ~9 chars; any one
    hi = min(len(text), pos + 8)  # containing *pos* must start in this window
    for m in _PH_RE.finditer(text, lo, hi):
        s, e = m.span()
        if s < pos < e:
            return e
    return pos


def estimate_tokens(text: str) -> int:
    """Rough token estimate (``~4 chars/token``) for observability."""
    return max(1, len(text or "") // CHARS_PER_TOKEN)


# ── Code-unit / ABI / fence protection ──


_CODE_UNIT_RE = re.compile(
    r"^\s*(?:(?:public|private|internal|external|pure|view|payable|virtual|override|"
    r"abstract|sealed|static|async|mut|pub|const|let|unsafe|extern)\s+)*"
    r"(?:contract|interface|library|function|constructor|modifier|receive|fallback|"
    r"event|error|struct|enum|class|trait|impl|def|fn)\b",
    re.MULTILINE,
)
_FENCE_RE = re.compile(r"^([`~]{3,})\s*(\S*)\s*$", re.MULTILINE)
_ABI_KEY_RE = re.compile(r'"(?:type|name|inputs|outputs|stateMutability)"')

# Inline code spans (`` `code` `` / reST `` ``code`` ``). Fenced blocks are
# handled separately by ``_fence_spans``; this protects short single-line
# spans so a chunk boundary never splits mid-span. Balanced 1-2 backtick
# delimiters with bounded content: triple-backtick fences and unmatched
# backticks never match here.
_CODE_SPAN_RE = re.compile(r"(?<!`)`{1,2}(?=[^`\n])([^`\n]{1,200})`{1,2}(?!`)")


def _inline_code_spans(text: str) -> list[tuple[int, int]]:
    """Find inline code spans (`` `foo` ``, `` ``bar`` ``) as atom spans."""
    return [m.span() for m in _CODE_SPAN_RE.finditer(text)]


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


# ── Heading detection ──


_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
_RST_HEADING_RE = re.compile(r"^([^\n]{2,60})\n[=\-~^+*]{3,}\s*$", re.MULTILINE)
_ADOC_HEADING_RE = re.compile(r"^([=]{1,5}|[-]{1,5})\s+(.*?)\s*$", re.MULTILINE)


def _heading_body_start(text: str, heading_start: int, kind: str) -> int:
    """Position just after the heading syntax (its line, + underline for reST)."""
    nl = text.find("\n", heading_start)
    if nl == -1:
        return len(text)
    if kind == "rst":
        nl2 = text.find("\n", nl + 1)
        return len(text) if nl2 == -1 else nl2 + 1
    return nl + 1


def _detect_headings(text: str) -> list[tuple[int, str, str, int]]:
    """Return ``(pos, label, marker, body_start)`` for headings.

    ``label`` is the plain heading text; ``marker`` is the original syntax
    (``##``, ``=`` …) used to rebuild the heading line as chunk context;
    ``body_start`` is where the section body begins (past the heading line).
    """
    out: list[tuple[int, str, str, int]] = []
    for m in _MD_HEADING_RE.finditer(text):
        label = m.group(2).strip()
        if label:
            out.append((m.start(), label, m.group(1), _heading_body_start(text, m.start(), "md")))
    for m in _RST_HEADING_RE.finditer(text):
        if any(abs(m.start() - pos) < 2 for pos, _, _, _ in out):
            continue
        # reST headings carry no inline marker; the text is the heading line.
        out.append((m.start(), m.group(1).strip(), "", _heading_body_start(text, m.start(), "rst")))
    for m in _ADOC_HEADING_RE.finditer(text):
        label = m.group(2).strip()
        if label and not any(abs(m.start() - pos) < 2 for pos, _, _, _ in out):
            out.append(
                (m.start(), label, m.group(1)[0], _heading_body_start(text, m.start(), "adoc"))
            )
    out.sort(key=lambda t: t[0])
    deduped: list[tuple[int, str, str, int]] = []
    for pos, label, marker, body_start in out:
        if deduped and abs(pos - deduped[-1][0]) < 2:
            continue
        deduped.append((pos, label, marker, body_start))
    return deduped


def _sectionize(text: str, title: str = "") -> list[tuple[str, str, str]]:
    """Split *text* into ``(section_body, heading_context, heading_label)``.

    When the document has headings, each section is chunked independently so
    content from different sections is never mixed in one chunk. The section
    body starts *after* the heading line; ``heading_context`` (the rebuilt
    heading line, e.g. ``# Audit``) is prepended to every chunk so each one
    carries its section context. Documents without headings are a single
    section whose context is the title.
    """
    headings = _detect_headings(text)
    if not headings:
        return [(text, title, title)]
    sections: list[tuple[str, str, str]] = []
    for i, (_pos, label, marker, body_start) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        sec = text[body_start:end]
        if sec.strip():
            sections.append((sec, f"{marker} {label}".strip(), label))
    if headings[0][0] > 0 and text[: headings[0][0]].strip():
        sections.insert(0, (text[: headings[0][0]], title, title))
    return sections


# ── Strategy implementations ──


def get_strategy(content_type: str) -> dict[str, Any]:
    """Get chunking strategy preset for a content type."""
    return STRATEGY_PRESETS.get(content_type, STRATEGY_PRESETS["default"])


def recursive_chunk(
    text: str,
    chunk_size: int = 512,
    overlap: float = 0.15,
    separators: list[str] | None = None,
    max_chars: int | None = None,
) -> list[str]:
    """
    Recursive character splitting - tries to split at natural boundaries.
    Separator hierarchy: paragraph → line → sentence → word → character.

    Crypto-aware: EVM/Solana addresses, hashes and URLs are never split in
    half (a boundary that would cut one is pushed past it).

    Args:
        text: Document text to chunk
        chunk_size: Target chunk size in characters (~4 chars per token)
        overlap: Fraction of chunk_size to overlap (0.0-0.25)
        separators: Custom separator hierarchy (defaults to RECURSIVE_SEPARATORS)
        max_chars: Hard cap on chunk length; defaults to the natural cap for
                   the separator set when not given (behavior-preserving).

    Returns:
        List of text chunks
    """
    if not text or len(text) < 50:
        return [text] if text else []

    if separators is None:
        separators = RECURSIVE_SEPARATORS

    max_sep = max((len(s) for s in separators), default=0)
    effective_max = max_chars if max_chars is not None else chunk_size + max_sep

    protected, atoms = _protect_atoms(text)
    overlap_chars = int(chunk_size * overlap)
    chunks: list[str] = []
    start = 0

    while start < len(protected):
        end = min(start + chunk_size, len(protected))

        # If this is the last chunk, just take the remainder
        if end >= len(protected):
            chunks.append(protected[start:])
            break

        # Try to find a natural split point within the chunk
        chunk_text = protected[start:end]
        split_pos: int | None = None

        for sep in separators:
            if not sep:
                # Empty separator = character-level split (last resort)
                split_pos = end
                break

            # Find the LAST occurrence of separator in the chunk
            # (prefer splitting at the end to keep chunks as large as possible)
            pos = chunk_text.rfind(sep)
            if pos > chunk_size * 0.5:  # Only split if it's in the latter half
                split_pos = start + pos + len(sep)
                break

        if split_pos is None:
            split_pos = end

        # Hard cap, then snap past any atom the boundary would cut.
        split_pos = min(split_pos, start + effective_max)
        split_pos = _snap_pos(protected, split_pos)

        chunks.append(protected[start:split_pos].strip())

        # Next chunk starts with overlap, never mid-atom.
        start = _snap_pos(protected, split_pos - overlap_chars)
        if start <= 0 or start >= len(protected):
            break

    # Restore addresses/hashes/URLs, then drop empties.
    restored = [_restore_atoms(c, atoms) for c in chunks]
    return [c for c in restored if len(c) > 20]


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


def sentence_chunk(
    text: str,
    chunk_size: int = 512,
    overlap: float = 0.15,
    max_chars: int | None = None,
) -> list[str]:
    """
    Sentence-aware chunking - never splits mid-sentence.
    Groups sentences to hit target chunk size. Crypto atoms are protected.
    """
    del max_chars  # sentences are indivisible; cap is documented in module header
    if not text:
        return []

    protected, atoms = _protect_atoms(text)

    # Split into sentences (handles abbreviations like "Dr.", "3.14", "0x...")
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", protected)
    if len(sentences) <= 1:
        return [_restore_atoms(c, atoms) for c in recursive_chunk(text, chunk_size, overlap)]

    chunks: list[str] = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) <= chunk_size:
            current += " " + sent if current else sent
        else:
            if current:
                chunks.append(current.strip())
            current = sent

    if current:
        chunks.append(current.strip())

    # Add overlap: prepend last ~15% of previous chunk to next
    if overlap > 0 and len(chunks) > 1:
        overlap_chars = int(chunk_size * overlap)
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            tail_start = _snap_pos(prev, max(0, len(prev) - overlap_chars))
            prev_tail = prev[tail_start:] if tail_start < len(prev) else prev
            overlapped.append(prev_tail + " " + chunks[i])
        chunks = overlapped

    return [_restore_atoms(c, atoms) for c in chunks]


def fixed_chunk(
    text: str,
    chunk_size: int = 128,
    overlap: float = 0.0,
    max_chars: int | None = None,
) -> list[str]:
    """Simple fixed-size chunking by character count; crypto atoms intact."""
    if not text:
        return []
    max_chars = max_chars or chunk_size
    protected, atoms = _protect_atoms(text)
    overlap_chars = int(chunk_size * overlap)
    step = max(chunk_size - overlap_chars, 1)
    chunks: list[str] = []
    for i in range(0, len(protected), step):
        end = min(i + chunk_size, len(protected))
        end = min(_snap_pos(protected, end), i + max_chars)
        if end <= i:
            continue
        chunks.append(protected[i:end].strip())
    restored = [_restore_atoms(c, atoms) for c in chunks]
    return [c for c in restored if len(c) > 10]


def chunk_document(
    text: str,
    content_type: str = "default",
    custom_strategy: dict[str, Any] | None = None,
) -> list[str]:
    """
    Chunk a document using the appropriate strategy for its content type.

    Args:
        text: Document text
        content_type: One of 'news', 'scam_report', 'contract_code',
                      'wallet_profile', 'annual_report', 'default'
        custom_strategy: Override with {'strategy': '...', 'chunk_size': N, 'overlap': F}

    Returns:
        List of text chunks
    """
    return [c["text"] for c in split_doc_into_chunks(text, content_type, custom_strategy)]


def split_doc_into_chunks(
    text: str,
    content_type: str = "default",
    custom_strategy: dict[str, Any] | None = None,
    *,
    doc_meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Crypto-aware, metadata-carrying chunking entry point.

    Same strategy selection as :func:`chunk_document`, plus:
      - section headings (markdown/reST/AsciiDoc) become boundaries and are
        prepended to each chunk as context;
      - each chunk dict carries ``section``, ``doc_type``, ``chunk_index``,
        ``total_chunks``, ``estimated_tokens`` and any ``doc_meta`` fields.

    Returns:
        List of chunk dicts: ``{"text", "section", "doc_type", "chunk_index",
        "total_chunks", "estimated_tokens", "source", "url", "title", ...}``
    """
    strategy = custom_strategy or get_strategy(content_type)
    size = int(strategy["chunk_size"])
    overlap = float(strategy["overlap"])
    max_chars = int(strategy.get("max_chars", size))
    strat_name = str(strategy["strategy"])

    meta = dict(doc_meta or {})
    title = meta.get("title", "")
    source = meta.get("source", "")
    url = meta.get("url", "")

    text = (text or "").strip()
    if not text:
        return []

    sections = _sectionize(text, title)
    chunks: list[dict[str, Any]] = []

    for sec_text, heading, label in sections:
        if strat_name == "code":
            parts = code_chunk(sec_text, size, overlap, max_chars)
        elif strat_name == "sentence":
            parts = sentence_chunk(sec_text, size, overlap, max_chars)
        elif strat_name == "fixed":
            parts = fixed_chunk(sec_text, size, overlap, max_chars)
        elif strat_name == "semantic":
            # Semantic chunking requires embedding every sentence - expensive.
            # Fall back to recursive for now; semantic can be added later.
            logger.info("Semantic chunking requested, falling back to recursive")
            parts = recursive_chunk(sec_text, size, overlap, max_chars=max_chars)
        else:
            parts = recursive_chunk(sec_text, size, overlap, max_chars=max_chars)

        for part in parts:
            content = f"{heading}\n{part}" if heading else part
            chunks.append(
                {
                    "text": content,
                    "section": label,
                    "doc_type": content_type,
                    "chunk_index": len(chunks),
                    "total_chunks": 0,  # backfilled below
                    "estimated_tokens": estimate_tokens(content),
                    "source": source,
                    "url": url,
                    "title": title,
                }
            )

    for c in chunks:
        c["total_chunks"] = len(chunks)
    return chunks


# ── Parent-Child Document Chunking ──


@dataclass
class ParentChunk:
    """One parent context window plus its search-oriented child chunks.

    Attributes:
        content: Parent context text — the full section (with its heading)
            when it fits the parent window budget, otherwise a sequential
            slice of the section. Children are subdivisions of this text.
        child_chunks: Small search-oriented chunks (same shape as
            :func:`split_doc_into_chunks` output) that each reference their
            parent via ``parent_id`` / ``parent_index`` / ``parent_total``
            metadata keys.
        metadata: Parent-level metadata: ``section``, ``doc_type``,
            ``parent_id``, ``parent_index``, ``parent_total``,
            ``estimated_tokens`` plus any ``doc_meta`` fields.
    """

    content: str
    child_chunks: list[dict[str, Any]]
    metadata: dict[str, Any]


def _parent_id(content: str) -> str:
    """Stable, content-addressed id for a parent window (dedup-safe)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def split_doc_parent_child(
    text: str,
    content_type: str = "default",
    custom_strategy: dict[str, Any] | None = None,
    *,
    doc_meta: dict[str, Any] | None = None,
    parent_token_target: int = 3000,
    parent_max_chars: int = 16000,
) -> list[ParentChunk]:
    """Parent-child split for retrieval-augmented pipelines.

    Produces small, search-oriented child chunks (~512 tokens, heading +
    metadata) that each reference a larger parent context window (the full
    section, or a 2-4k-token slice when the section is oversized). Use with a
    parent retriever: match on children, then expand to ``parent.content``
    for the LLM context window.

    Additive API — :func:`split_doc_into_chunks` and :func:`chunk_document`
    are unchanged.

    Args:
        text: Document text to chunk.
        content_type: Strategy preset (same values as
            :func:`split_doc_into_chunks`).
        custom_strategy: Override with ``{'strategy': ..., 'chunk_size': N,
            'overlap': F}``.
        doc_meta: Document metadata copied onto parents and children
            (``title``, ``source``, ``url``, …).
        parent_token_target: Desired parent window size in tokens (~4
            chars/token) used when a section must be split into windows.
        parent_max_chars: Hard cap on a single parent window's length.

    Returns:
        List of :class:`ParentChunk`; empty for blank input. ``chunk_index``
        / ``total_chunks`` on child chunks are relative to their parent;
        ``parent_index`` / ``parent_total`` are relative to their section.
    """
    meta = dict(doc_meta or {})
    title = meta.get("title", "")
    source = meta.get("source", "")
    url = meta.get("url", "")

    text = (text or "").strip()
    if not text:
        return []

    parent_target_chars = max(parent_token_target, 1) * CHARS_PER_TOKEN
    parent_max = max(parent_max_chars, 1)
    sections = _sectionize(text, title)
    parents_out: list[ParentChunk] = []

    for sec_text, heading, label in sections:
        section_context = f"{heading}\n{sec_text}" if heading else sec_text
        if len(section_context) <= parent_max:
            windows = [section_context]
        else:
            # Oversized section: several sequential parent windows, each
            # still carrying the section heading as context. No overlap —
            # parents are context windows, not search chunks.
            parts = recursive_chunk(
                sec_text,
                chunk_size=parent_target_chars,
                overlap=0.0,
                max_chars=parent_max,
            )
            windows = [f"{heading}\n{p}" if heading else p for p in parts if p.strip()]

        for p_idx, parent_text in enumerate(windows):
            parent_id = _parent_id(parent_text)
            children = split_doc_into_chunks(
                parent_text, content_type, custom_strategy, doc_meta=meta
            )
            for child in children:
                child["parent_id"] = parent_id
                child["parent_index"] = p_idx
                child["parent_total"] = len(windows)
            parents_out.append(
                ParentChunk(
                    content=parent_text,
                    child_chunks=children,
                    metadata={
                        "section": label,
                        "doc_type": content_type,
                        "parent_id": parent_id,
                        "parent_index": p_idx,
                        "parent_total": len(windows),
                        "estimated_tokens": estimate_tokens(parent_text),
                        "source": source,
                        "url": url,
                        "title": title,
                    },
                )
            )
    return parents_out


# ── Content Hash Dedup ──


def content_hash(text: str) -> str:
    """MD5 hash of normalized text for dedup."""
    # Normalize: lowercase, strip extra whitespace, remove punctuation noise
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()


async def is_duplicate(collection: str, text: str, redis_client: Any | None = None) -> bool:
    """Check if content already exists in the collection via hash dedup."""
    if redis_client is None:
        from rugbrain.search_service import _get_redis

        redis_client = await _get_redis()
    assert redis_client is not None, "redis_client required for dedup"

    h = content_hash(text)
    key = f"rag:dedup:{collection}:{h}"
    exists = await redis_client.exists(key)
    return bool(exists)


async def mark_ingested(
    collection: str,
    text: str,
    ttl: int = 604800,  # 7 days default
    redis_client: Any | None = None,
) -> None:
    """Mark content as ingested in dedup registry."""
    if redis_client is None:
        from rugbrain.search_service import _get_redis

        redis_client = await _get_redis()
    assert redis_client is not None, "redis_client required for mark_ingested"

    h = content_hash(text)
    key = f"rag:dedup:{collection}:{h}"
    await redis_client.setex(key, ttl, "1")


# ── Quality Scoring ──


def quality_score(text: str, content_type: str = "default") -> int:
    """
    Score content quality 0-100. Skip docs below threshold (default: 30).

    Factors:
    - Length (too short = low signal)
    - Structure (has paragraphs, headings)
    - Entity density (addresses, token symbols, chain names)
    - Noise ratio (special characters, repeated patterns)
    """
    if not text or len(text) < 50:
        return 0

    score = 50  # Start neutral

    # Length bonus
    if len(text) > 500:
        score += 15
    elif len(text) > 200:
        score += 10
    elif len(text) > 100:
        score += 5

    # Structure bonus (paragraphs, headings)
    if "\n\n" in text:
        score += 10
    if re.search(r"^#{1,3}\s", text, re.MULTILINE):
        score += 5

    # Entity density (crypto-specific)
    entities = 0
    entities += len(re.findall(r"0x[a-fA-F0-9]{40}", text))  # ETH addresses
    entities += len(re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", text))  # Solana addresses
    entities += len(re.findall(r"\b[A-Z]{2,5}\b", text))  # Token symbols
    entities += len(
        re.findall(r"(?i)(ethereum|solana|bitcoin|polygon|arbitrum|bsc|avalanche|tron)", text)
    )
    score += min(entities * 2, 20)  # Cap at +20

    # Noise penalty
    noise_chars = len(re.findall(r"[^\w\s.,;:!?\-()\[\]{}$@%&*+/<=>|~^]", text))
    noise_ratio = noise_chars / max(len(text), 1)
    if noise_ratio > 0.3:
        score -= 20
    elif noise_ratio > 0.15:
        score -= 10

    # Repeated pattern penalty (spam)
    words = text.lower().split()
    if len(words) > 10:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.3:
            score -= 25
        elif unique_ratio < 0.5:
            score -= 10

    return max(0, min(100, score))


# ── Batch Chunking for Ingestion ──


def chunk_batch(
    documents: list[dict[str, Any]],
    content_type: str = "default",
    min_quality: int = 30,
) -> list[dict[str, Any]]:
    """
    Process a batch of documents: chunk, score quality, filter.

    Each document should have: {'text': str, 'source': str, 'url': str, ...}
    Returns list of chunk dicts with metadata preserved, plus per-chunk
    ``section`` / ``doc_type`` / ``estimated_tokens`` fields.
    """
    chunks: list[dict[str, Any]] = []
    skipped = 0

    for doc in documents:
        text = doc.get("text", doc.get("title", ""))
        if not text:
            skipped += 1
            continue

        # Quality filter
        score = quality_score(text, content_type)
        if score < min_quality:
            skipped += 1
            continue

        # Chunk (crypto-aware, metadata-carrying)
        doc_meta = {k: v for k, v in doc.items() if k not in ("text", "title")}
        doc_chunks = split_doc_into_chunks(text, content_type, doc_meta=doc_meta)
        for i, chunk in enumerate(doc_chunks):
            chunks.append(
                {
                    "text": chunk["text"],
                    "chunk_index": i,
                    "total_chunks": len(doc_chunks),
                    "quality_score": score,
                    "content_hash": content_hash(chunk["text"]),
                    "section": chunk.get("section", ""),
                    "doc_type": content_type,
                    "estimated_tokens": chunk.get("estimated_tokens", 0),
                    "source": doc.get("source", "unknown"),
                    "url": doc.get("url", ""),
                    "category": doc.get("category", content_type),
                    "date": doc.get("date", ""),
                    "chain": doc.get("chain", ""),
                    "tags": doc.get("tags", []),
                    **{k: v for k, v in doc.items() if k not in ("text", "title")},
                }
            )

    logger.info("Chunked %s docs → %s chunks (%s skipped)", len(documents), len(chunks), skipped)
    return chunks
