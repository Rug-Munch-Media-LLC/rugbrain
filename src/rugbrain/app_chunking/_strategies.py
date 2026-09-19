"""Chunking strategy implementations.

``recursive`` (paragraph → line → sentence → word → character), ``code``
(structure-aware), ``sentence`` (never mid-sentence) and ``fixed``
(character count). All protect crypto atoms across boundaries.
"""

from __future__ import annotations

import re
from typing import Any

from rugbrain.app_chunking._atoms import _protect_atoms, _restore_atoms, _snap_pos

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
