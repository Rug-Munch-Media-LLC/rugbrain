#!/usr/bin/env python3
"""
CONTEXTUAL CHUNKING - Anthropic-style Contextual Retrieval
==========================================================
Prepends each chunk with a short LLM-generated context string explaining
where this chunk sits in the source document. Dramatically improves
retrieval accuracy (Anthropic: 65% → 89% top-20).

Pipeline:
  1. Chunk document (semantic or fixed-size with overlap)
  2. For each chunk, generate context via cheap LLM:
     "Here is the full document: <doc>{whole_doc}</doc>
      Here is the chunk we want to situate: <chunk>{chunk}</chunk>
      Give a short context (2-3 sentences) to precede this chunk
      that improves its retrievability."
  3. Concatenate: context_text + chunk_text → embed this
  4. Store original chunk_text separately for generation (LLM context window)

Supports:
  - Batch processing (multiple chunks per LLM call for efficiency)
  - Fallback to heuristic context when LLM unavailable
  - Content hashing for dedup + cache
  - Parent-child chunking: small child chunks for retrieval, parent section for LLM
"""

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── LLM Config ────────────────────────────────────────────────────────
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
# Decode base64 LLM key if present, otherwise use plain LLM_API_KEY
if os.getenv("LLM_API_KEY_B64"):
    import base64 as _b64

    os.environ["LLM_API_KEY"] = _b64.b64decode(os.getenv("LLM_API_KEY_B64")).decode()
LLM_API_KEY = os.getenv("LLM_API_KEY", OPENROUTER_KEY)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
AI_BASE = LLM_BASE_URL if LLM_API_KEY else "https://openrouter.ai/api/v1/chat/completions"
CONTEXT_MODEL = os.getenv("CONTEXT_CHUNK_MODEL", os.getenv("LLM_MODEL", "deepseek-v4-flash"))

# ── Chunking defaults ─────────────────────────────────────────────────
DEFAULT_CHUNK_SIZE = 2500  # chars per chunk
DEFAULT_OVERLAP = 300  # overlap between chunks
DEFAULT_MAX_CHUNKS = 50  # safety cap per document


@dataclass
class Chunk:
    """A single chunk with optional context."""

    index: int
    content: str  # original chunk text
    contextualized: str = ""  # context + content (for embedding)
    context_text: str = ""  # LLM-generated context (2-3 sentences)
    parent_id: str | None = None  # parent section ID (for parent-child pattern)
    parent_content: str | None = None  # full parent section text
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]


@dataclass
class ChunkedDocument:
    """Result of chunking a document."""

    doc_id: str
    source: str  # source filename/URL
    chunks: list[Chunk]
    total_chars: int = 0
    processing_time_ms: float = 0.0


# ══════════════════════════════════════════════════════════════════════
# CHUNKING
# ══════════════════════════════════════════════════════════════════════


def _is_solidity(text: str) -> bool:
    """Detect if text is a Solidity smart contract."""
    indicators = [
        "pragma solidity",
        "contract ",
        "function ",
        "// SPDX-License-Identifier",
    ]
    head = text[:500].lower()
    return any(ind in head for ind in indicators)


def chunk_document(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    respect_boundaries: bool = True,
) -> list[Chunk]:
    """
    Split document into overlapping chunks.
    When respect_boundaries=True, splits at paragraph/sentence boundaries
    near the target chunk_size instead of cutting mid-sentence.

    For Solidity smart contracts, uses AST-aware chunking (function/contract
    boundaries) instead of character-level splits. Critical for code security
    analysis where splitting a function breaks its logic.
    """
    if not text or len(text) <= chunk_size:
        return [Chunk(index=0, content=text)]

    # ── Solidity AST-aware chunking ──
    if _is_solidity(text):
        try:
            from rmi_rag.solidity_chunker import chunk_solidity_ast

            ast_chunks = chunk_solidity_ast(text)
            if ast_chunks:
                return [Chunk(index=i, content=c) for i, c in enumerate(ast_chunks[:max_chunks])]
        except ImportError:
            pass
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    chunks = []
    start = 0
    idx = 0

    while start < len(text) and idx < max_chunks:
        end = start + chunk_size

        if end >= len(text):
            # Last chunk - take everything remaining
            chunk_text = text[start:].strip()
            if chunk_text:
                chunks.append(Chunk(index=idx, content=chunk_text))
            break

        if respect_boundaries:
            # Look for a paragraph break or sentence end near the target end
            # Search backwards from end for '\n\n' or '. ' or '.\n'
            search_start = max(start + chunk_size // 2, end - 200)
            search_end = min(end + 100, len(text))

            # Prefer paragraph break
            para_break = text.rfind("\n\n", search_start, search_end)
            if para_break == -1:
                para_break = text.rfind("\n", search_start, search_end)

            if para_break != -1 and para_break > start:
                end = para_break + 1
            else:
                # Fall back to sentence boundary
                for delim in [". ", ".\n", "! ", "? ", ";\n"]:
                    sent_break = text.rfind(delim, search_start, search_end)
                    if sent_break != -1 and sent_break > start:
                        end = sent_break + len(delim)
                        break

        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(Chunk(index=idx, content=chunk_text))
            idx += 1

        start = end - overlap if end < len(text) else end

    return chunks


# ══════════════════════════════════════════════════════════════════════
# CONTEXT GENERATION
# ══════════════════════════════════════════════════════════════════════

CONTEXT_PROMPT = """Here is the full document:
<doc>
{doc}
</doc>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Give a short context (2-3 sentences) to precede this chunk that improves its retrievability. The context should explain where this chunk fits in the document and what information it contains. Be specific - mention names, topics, and any key entities. Do NOT repeat the chunk content verbatim.

Context:"""


async def _generate_context_llm(
    doc_text: str,
    chunk_text: str,
    model: str = CONTEXT_MODEL,
) -> str:
    """Generate contextual preamble for a chunk using LLM."""
    if not LLM_API_KEY:
        return ""

    prompt = CONTEXT_PROMPT.format(
        doc=doc_text[:8000],  # truncate full doc to fit context
        chunk=chunk_text[:1500],
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                AI_BASE,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            context = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return context.strip()
    except Exception as e:
        logger.warning(f"Context generation failed: {e}")
        return ""


def _generate_heuristic_context(
    doc_text: str,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
) -> str:
    """
    Fallback: generate context heuristically when LLM is unavailable.
    Extracts title, section headers, and position info.
    """
    # Try to find a title (first line or # header)
    lines = doc_text.strip().split("\n")
    title = ""
    for line in lines[:5]:
        if line.strip().startswith("#"):
            title = line.strip().lstrip("#").strip()
            break
        elif len(line.strip()) > 10:
            title = line.strip()[:100]
            break

    # Find section headers near this chunk
    doc_before = doc_text[: doc_text.find(chunk_text[:50])] if chunk_text[:50] in doc_text else ""
    section = ""
    for line in reversed(doc_before.split("\n")):
        if line.strip().startswith("#"):
            section = line.strip().lstrip("#").strip()
            break

    parts = []
    if title:
        parts.append(f"From: {title}")
    if section and section != title:
        parts.append(f"Section: {section}")
    parts.append(f"Chunk {chunk_index + 1} of {total_chunks}")

    return ". ".join(parts) + "."


async def contextualize_chunks(
    doc_text: str,
    chunks: list[Chunk],
    use_llm: bool = True,
    batch_size: int = 5,
) -> list[Chunk]:
    """
    Add contextual preambles to all chunks.
    Uses LLM when available, falls back to heuristic context.
    Processes in small batches to be gentle on API rate limits.
    """
    total = len(chunks)

    for i in range(0, total, batch_size):
        batch = chunks[i : i + batch_size]
        tasks = []

        for chunk in batch:
            if use_llm and LLM_API_KEY:
                tasks.append(_generate_context_llm(doc_text, chunk.content))
            else:
                tasks.append(None)

        if any(t is not None for t in tasks):
            # Only call gather if we have actual async tasks
            actual_tasks = [t for t in tasks if t is not None]
            actual_results = await asyncio.gather(*actual_tasks, return_exceptions=True)
            result_idx = 0
            for j, chunk in enumerate(batch):
                if tasks[j] is not None:
                    ctx = actual_results[result_idx]
                    result_idx += 1
                    if isinstance(ctx, Exception) or not ctx:
                        ctx = _generate_heuristic_context(doc_text, chunk.content, chunk.index, total)
                    chunk.context_text = ctx
                    chunk.contextualized = f"{ctx}\n\n{chunk.content}"
                else:
                    ctx = _generate_heuristic_context(doc_text, chunk.content, chunk.index, total)
                    chunk.context_text = ctx
                    chunk.contextualized = f"{ctx}\n\n{chunk.content}"
        else:
            # All heuristic (no LLM)
            for chunk in batch:
                ctx = _generate_heuristic_context(doc_text, chunk.content, chunk.index, total)
                chunk.context_text = ctx
                chunk.contextualized = f"{ctx}\n\n{chunk.content}"

    return chunks


# ══════════════════════════════════════════════════════════════════════
# FULL PIPELINE
# ══════════════════════════════════════════════════════════════════════


async def process_document(
    text: str,
    doc_id: str = "",
    source: str = "",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    use_llm_context: bool = True,
) -> ChunkedDocument:
    """
    Full contextual chunking pipeline:
    1. Chunk the document
    2. Generate contextual preambles for each chunk
    3. Return ChunkedDocument with all chunks ready for embedding

    The `contextualized` field on each chunk is what you embed.
    The `content` field is what you feed to the LLM at generation time.
    """
    import time

    start = time.time()

    if not doc_id:
        doc_id = hashlib.sha256(text.encode()).hexdigest()[:16]

    # Step 1: Chunk
    chunks = chunk_document(text, chunk_size=chunk_size, overlap=overlap)

    # Step 2: Contextualize
    chunks = await contextualize_chunks(text, chunks, use_llm=use_llm_context)

    elapsed = (time.time() - start) * 1000

    return ChunkedDocument(
        doc_id=doc_id,
        source=source,
        chunks=chunks,
        total_chars=len(text),
        processing_time_ms=round(elapsed, 1),
    )


# ══════════════════════════════════════════════════════════════════════
# PARENT-CHILD CHUNKING
# ══════════════════════════════════════════════════════════════════════


def parent_child_chunk(
    text: str,
    parent_size: int = 4000,
    child_size: int = 800,
    overlap: int = 200,
) -> list[Chunk]:
    """
    Parent-child chunking pattern:
    - Large 'parent' chunks (4K chars) for LLM context at generation time
    - Small 'child' chunks (800 chars) for retrieval precision
    - Each child knows its parent so we can retrieve the parent when a child matches

    Returns a flat list: parent chunks + their children.
    Parents have metadata.is_parent=True, children have parent_id set.
    """
    # Create parent sections first
    parent_chunks = chunk_document(text, chunk_size=parent_size, overlap=overlap)

    all_chunks = []
    for p_idx, parent in enumerate(parent_chunks):
        parent_id = f"parent_{p_idx}"
        parent.metadata = {"is_parent": True, "child_count": 0}
        all_chunks.append(parent)

        # Split parent into children
        children = chunk_document(parent.content, chunk_size=child_size, overlap=100)
        for c in children:
            c.parent_id = parent_id
            c.parent_content = parent.content
            c.index = len(all_chunks)
            all_chunks.append(c)

        parent.metadata["child_count"] = len(children)

    return all_chunks
