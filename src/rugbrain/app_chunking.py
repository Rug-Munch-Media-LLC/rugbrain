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

Standards (2026 consensus):
  - Default: recursive, 512 tokens, 15% overlap
  - News: recursive, 512 tokens, 15% overlap
  - Scam reports: semantic, 400 tokens, 10% overlap
  - Contract code: code-aware, 256 tokens, 20% overlap
  - Wallet profiles: fixed, 128 tokens, 0% overlap
  - Annual reports: recursive, 800 tokens, 15% overlap
"""

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger("rag.chunking")

# ── Strategy presets per content type ──
STRATEGY_PRESETS = {
    "news": {"strategy": "recursive", "chunk_size": 512, "overlap": 0.15},
    "scam_report": {"strategy": "semantic", "chunk_size": 400, "overlap": 0.10},
    "contract_code": {"strategy": "code", "chunk_size": 256, "overlap": 0.20},
    "wallet_profile": {"strategy": "fixed", "chunk_size": 128, "overlap": 0.0},
    "annual_report": {"strategy": "recursive", "chunk_size": 800, "overlap": 0.15},
    "default": {"strategy": "recursive", "chunk_size": 512, "overlap": 0.15},
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
) -> list[str]:
    """
    Recursive character splitting - tries to split at natural boundaries.
    Separator hierarchy: paragraph → line → sentence → word → character.

    Args:
        text: Document text to chunk
        chunk_size: Target chunk size in characters (~4 chars per token)
        overlap: Fraction of chunk_size to overlap (0.0-0.25)
        separators: Custom separator hierarchy (defaults to RECURSIVE_SEPARATORS)

    Returns:
        List of text chunks
    """
    if not text or len(text) < 50:
        return [text] if text else []

    if separators is None:
        separators = RECURSIVE_SEPARATORS

    overlap_chars = int(chunk_size * overlap)
    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # If this is the last chunk, just take the remainder
        if end >= len(text):
            chunks.append(text[start:])
            break

        # Try to find a natural split point within the chunk
        chunk_text = text[start:end]
        split_pos = None

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

        chunks.append(text[start:split_pos].strip())

        # Next chunk starts with overlap
        start = split_pos - overlap_chars
        if start <= 0 or start >= len(text):
            break

    # Remove empty chunks
    return [c for c in chunks if len(c) > 20]


def code_chunk(
    code: str,
    chunk_size: int = 256,
    overlap: float = 0.20,
) -> list[str]:
    """Structure-aware chunking for smart contract code."""
    return recursive_chunk(code, chunk_size, overlap, CODE_SEPARATORS)


def sentence_chunk(
    text: str,
    chunk_size: int = 512,
    overlap: float = 0.15,
) -> list[str]:
    """
    Sentence-aware chunking - never splits mid-sentence.
    Groups sentences to hit target chunk size.
    """
    if not text:
        return []

    # Split into sentences (handles abbreviations like "Dr.", "3.14", "0x...")
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    if len(sentences) <= 1:
        return recursive_chunk(text, chunk_size, overlap)

    chunks = []
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
            prev_tail = (
                chunks[i - 1][-overlap_chars:]
                if len(chunks[i - 1]) > overlap_chars
                else chunks[i - 1]
            )
            overlapped.append(prev_tail + " " + chunks[i])
        return overlapped

    return chunks


def fixed_chunk(
    text: str,
    chunk_size: int = 128,
    overlap: float = 0.0,
) -> list[str]:
    """Simple fixed-size chunking by character count."""
    if not text:
        return []
    overlap_chars = int(chunk_size * overlap)
    chunks = []
    for i in range(0, len(text), chunk_size - overlap_chars):
        chunks.append(text[i : i + chunk_size].strip())
    return [c for c in chunks if len(c) > 10]


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
    strategy = custom_strategy or get_strategy(content_type)
    strat_name = strategy["strategy"]
    size = strategy["chunk_size"]
    overlap = strategy["overlap"]

    if strat_name == "recursive":
        return recursive_chunk(text, size, overlap)
    elif strat_name == "code":
        return code_chunk(text, size, overlap)
    elif strat_name == "sentence":
        return sentence_chunk(text, size, overlap)
    elif strat_name == "fixed":
        return fixed_chunk(text, size, overlap)
    elif strat_name == "semantic":
        # Semantic chunking requires embedding every sentence - expensive.
        # Fall back to recursive for now; semantic can be added later.
        logger.info("Semantic chunking requested, falling back to recursive")
        return recursive_chunk(text, size, overlap)
    else:
        return recursive_chunk(text, size, overlap)


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
    Returns list of chunk dicts with metadata preserved.
    """
    chunks = []
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

        # Chunk
        doc_chunks = chunk_document(text, content_type)
        for i, chunk in enumerate(doc_chunks):
            chunks.append(
                {
                    "text": chunk,
                    "chunk_index": i,
                    "total_chunks": len(doc_chunks),
                    "quality_score": score,
                    "content_hash": content_hash(chunk),
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
