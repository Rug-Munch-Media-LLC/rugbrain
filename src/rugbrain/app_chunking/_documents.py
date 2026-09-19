"""Document-level chunking: strategy dispatch and metadata.

``split_doc_into_chunks`` is the crypto-aware, metadata-carrying entry
point; ``chunk_document`` is the plain ``list[str]`` wrapper.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.app_chunking._atoms import estimate_tokens
from rugbrain.app_chunking._code_strategy import code_chunk
from rugbrain.app_chunking._headings import _sectionize
from rugbrain.app_chunking._strategies import (
    fixed_chunk,
    get_strategy,
    recursive_chunk,
    sentence_chunk,
)

logger = logging.getLogger("rag.chunking")


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
