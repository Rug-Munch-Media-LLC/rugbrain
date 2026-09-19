"""Crypto-atom protection: keep addresses/hashes/URLs intact across splits.

Spans matching the atom patterns are swapped out for tiny placeholders
before splitting and restored afterwards, so a chunk boundary never lands
mid-address. Two placeholder namespaces exist so nested protection passes
never collide: ``\\x00K<idx>\\x00`` (prose atoms from :func:`_protect_atoms`)
and ``\\x00KC<idx>\\x00`` (code-unit atoms from ``code_chunk``'s
pre-protection pass).
"""

from __future__ import annotations

import re

# ── Token budget ──
CHARS_PER_TOKEN = 4  # conservative proxy (~4 chars/token)
DEFAULT_TOKEN_TARGET = 512  # embedding-model sweet spot (tokens)
ABSOLUTE_MAX_CHARS = 8000  # embedder truncation guard (see embed paths)

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

# Inline code spans (`` `code` `` / reST `` ``code`` ``). Fenced blocks are
# handled separately by ``_fence_spans``; this protects short single-line
# spans so a chunk boundary never splits mid-span. Balanced 1-2 backtick
# delimiters with bounded content: triple-backtick fences and unmatched
# backticks never match here.
_CODE_SPAN_RE = re.compile(r"(?<!`)`{1,2}(?=[^`\n])([^`\n]{1,200})`{1,2}(?!`)")


def _inline_code_spans(text: str) -> list[tuple[int, int]]:
    """Find inline code spans (`` `foo` ``, `` ``bar`` ``) as atom spans."""
    return [m.span() for m in _CODE_SPAN_RE.finditer(text)]


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
