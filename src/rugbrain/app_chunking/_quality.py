"""Quality scoring and batch chunking for ingestion.

``quality_score`` rates content 0-100 (length, structure, entity density,
noise); ``chunk_batch`` chunks, scores and filters a document batch.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from rugbrain.app_chunking._dedup import content_hash
from rugbrain.app_chunking._documents import split_doc_into_chunks

logger = logging.getLogger("rag.chunking")


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
