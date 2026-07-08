"""M3 RAG Chunking - recursive text chunking with MD5 dedup.

Why chunking matters:
- Embedding models have context limits (bge-m3 = 8192 tokens, but we
  chunk to 512 for granularity and to keep individual FAISS hits useful)
- Smaller chunks = better retrieval precision (less noise per result)
- Dedup via content MD5 prevents the same fact being ingested N times

Strategy (per 2026 RAG standards):
- Recursive character split, paragraph → sentence → word boundaries
- Overlap window preserves cross-chunk context
- Quality score: (length / max_chunk) x (1 - special_char_ratio)  # noqa: RUF002
- Skip chunks < min_chars (likely noise)
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_MAX_CHUNK = 512
DEFAULT_OVERLAP = 64
DEFAULT_MIN_CHARS = 32

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        from app.core.redis import get_redis
        _redis = get_redis()
    return _redis


@dataclass
class Chunk:
    """A piece of text ready to embed."""

    text: str
    content_hash: str
    index: int
    quality: float = 1.0


def content_hash(text: str) -> str:
    """Stable MD5 of normalized text. Used for dedup."""
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()


def chunk_text(
    text: str,
    max_chunk: int = DEFAULT_MAX_CHUNK,
    overlap: int = DEFAULT_OVERLAP,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[Chunk]:
    """Recursive character chunker.

    Splits on paragraph breaks first, then sentence, then word. Each split
    is greedy-packed into chunks of <= max_chunk chars, with `overlap` chars
    carried forward to preserve context.
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_chunk:
        h = content_hash(text)
        return [Chunk(text=text, content_hash=h, index=0, quality=_quality(text, max_chunk))]

    # Build sentence-level segments (split on . ! ? \n)
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        # If a single sentence is too long, hard-split on words
        if len(s) > max_chunk:
            words = s.split()
            sub = ""
            for w in words:
                if len(sub) + len(w) + 1 > max_chunk and sub:
                    chunks.append(sub)
                    sub = w
                else:
                    sub = (sub + " " + w).strip()
            if sub:
                chunks.append(sub)
        elif len(cur) + len(s) + 1 > max_chunk:
            if cur:
                chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()

    if cur:
        chunks.append(cur)

    # Apply overlap: each chunk gets the last `overlap` chars of the prior chunk as prefix
    if overlap > 0 and len(chunks) > 1:
        overlapped: list[str] = []
        for i, c in enumerate(chunks):
            if i == 0:
                overlapped.append(c)
            else:
                tail = chunks[i - 1][-overlap:]
                overlapped.append(tail + " " + c)
        chunks = overlapped

    # Filter and produce Chunk objects
    out: list[Chunk] = []
    for i, c in enumerate(chunks):
        if len(c) < min_chars:
            continue
        out.append(
            Chunk(
                text=c,
                content_hash=content_hash(c),
                index=i,
                quality=_quality(c, max_chunk),
            )
        )
    return out


def _quality(text: str, max_chunk: int) -> float:
    """Quick quality score 0-1. Penalize noise (high special-char ratio)."""
    if not text:
        return 0.0
    n = len(text)
    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
    length_score = min(1.0, n / max_chunk)
    special_score = max(0.0, 1.0 - (special / n))
    return round(0.7 * length_score + 0.3 * special_score, 3)


# ── Dedup via Redis ─────────────────────────────────────────────────
def is_duplicate(content_hash: str, collection: str) -> bool:
    """Has this exact content already been ingested into the collection?"""
    try:
        return bool(_get_redis().sismember(f"rag:hashes:{collection}", content_hash))
    except Exception as e:
        log.debug("dedup_check_failed: %s", e)
        return False


def mark_ingested(content_hash: str, collection: str) -> None:
    """Record that this content hash is now in the collection."""
    try:
        _get_redis().sadd(f"rag:hashes:{collection}", content_hash)
    except Exception as e:
        log.debug("dedup_mark_failed: %s", e)
