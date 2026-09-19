"""Parent-child document chunking for parent-retriever pipelines.

``split_doc_parent_child`` returns small search-oriented child chunks that
each reference a larger parent context window via ``parent_id``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from rugbrain.app_chunking._atoms import CHARS_PER_TOKEN, estimate_tokens
from rugbrain.app_chunking._documents import split_doc_into_chunks
from rugbrain.app_chunking._headings import _sectionize
from rugbrain.app_chunking._strategies import recursive_chunk


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
