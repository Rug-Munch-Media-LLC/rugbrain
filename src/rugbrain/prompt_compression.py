"""
Prompt compression + "lost in the middle" mitigation for RugBrain RAG.

Self-contained, additive library that squeezes long retrieved contexts into
the small context windows of quantized / low-parameter models. Pure library:
the search/ and ingest_sources/ lanes are untouched; callers opt in.

* ``compress_context`` — LLMLingua-style compression. Uses the ``llmlingua``
  package when installed (lazy import; model loads on first use, stays
  resident). Falls back to a lightweight extractive compressor that keeps
  high-signal sentences (code blocks, addresses, numbers, vulnerability
  terms, section headers) and drops filler, keeping code and addresses
  intact.
* ``layout_context`` / ``pack_context`` — "lost in the middle" mitigation:
  highest-relevance chunks FIRST (top of window), lower-relevance in the
  middle, user query LAST. Works with rerank scores and retrieval order.

Intended call order: retrieve -> rerank -> compress -> invert -> prompt::

    results = await search_similar(...)                      # 1. retrieve
    results = await rerank(query, results)                   # 2. rerank
    results = compress_context(results, 2048, query=query)   # 3. compress
    prompt  = pack_context(results, query)                   # 4. invert

Environment: ``LLMLINGUA_MODEL`` overrides the llmlingua HF model id
(default: microsoft/llmlingua-2-xlm-roberta-large-meeting-bank).
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Callable, Sequence
from typing import Any

from rugbrain.prompt_compression_pkg.chunks import (
    _SCORE_KEYS as _SCORE_KEYS,
)
from rugbrain.prompt_compression_pkg.chunks import (
    _annotate as _annotate,
)
from rugbrain.prompt_compression_pkg.chunks import (
    _chunk_score as _chunk_score,
)
from rugbrain.prompt_compression_pkg.chunks import (
    _distribute_budget as _distribute_budget,
)
from rugbrain.prompt_compression_pkg.chunks import (
    _get_text as _get_text,
)
from rugbrain.prompt_compression_pkg.chunks import (
    _set_text as _set_text,
)
from rugbrain.prompt_compression_pkg.llmlingua_lane import (
    LLMLINGUA_MODEL as LLMLINGUA_MODEL,
)
from rugbrain.prompt_compression_pkg.llmlingua_lane import (
    _get_llmlingua_compressor as _get_llmlingua_compressor,
)
from rugbrain.prompt_compression_pkg.llmlingua_lane import (
    _llmlingua_compress as _llmlingua_compress,
)
from rugbrain.prompt_compression_pkg.signals import (
    _ADDRESS_RE as _ADDRESS_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _BOILERPLATE_RE as _BOILERPLATE_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _CHARS_PER_TOKEN as _CHARS_PER_TOKEN,
)
from rugbrain.prompt_compression_pkg.signals import (
    _CODE_BLOCK_SCORE as _CODE_BLOCK_SCORE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _CODE_KEYWORD_RE as _CODE_KEYWORD_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _FENCED_CODE_RE as _FENCED_CODE_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _FILLER_START_RE as _FILLER_START_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _HAS_NUMBER_RE as _HAS_NUMBER_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _HEADER_RE as _HEADER_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _INLINE_CODE_RE as _INLINE_CODE_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _PLACEHOLDER_RE as _PLACEHOLDER_RE,
)
from rugbrain.prompt_compression_pkg.signals import (
    _VULN_TERMS as _VULN_TERMS,
)
from rugbrain.prompt_compression_pkg.signals import (
    _estimate_tokens as _estimate_tokens,
)
from rugbrain.prompt_compression_pkg.signals import (
    _PromptUnit as _PromptUnit,
)
from rugbrain.prompt_compression_pkg.signals import (
    _protect_spans as _protect_spans,
)
from rugbrain.prompt_compression_pkg.signals import (
    _score_unit as _score_unit,
)
from rugbrain.prompt_compression_pkg.signals import (
    _split_sentences as _split_sentences,
)
from rugbrain.prompt_compression_pkg.signals import (
    _unprotect as _unprotect,
)
from rugbrain.prompt_compression_pkg.units import (
    _compress_text as _compress_text,
)
from rugbrain.prompt_compression_pkg.units import (
    _extract_units as _extract_units,
)
from rugbrain.prompt_compression_pkg.units import (
    _extractive_compress as _extractive_compress,
)
from rugbrain.prompt_compression_pkg.units import (
    _select_units as _select_units,
)
from rugbrain.prompt_compression_pkg.units import (
    _slice_sentence as _slice_sentence,
)
from rugbrain.prompt_compression_pkg.units import (
    _truncate_text_head as _truncate_text_head,
)
from rugbrain.prompt_compression_pkg.units import (
    _truncate_unit as _truncate_unit,
)

logger = logging.getLogger(__name__)

#: Probed on THIS module so ``monkeypatch.setattr(
#: "rugbrain.prompt_compression.LLMLINGUA_AVAILABLE", False)`` keeps working.
LLMLINGUA_AVAILABLE = importlib.util.find_spec("llmlingua") is not None

__all__ = [
    "LLMLINGUA_AVAILABLE",
    "LLMLINGUA_MODEL",
    "compress_context",
    "layout_context",
    "pack_context",
]


def compress_context(
    chunks: Sequence[dict[str, Any]],
    target_tokens: int,
    *,
    query: str | None = None,
    count_tokens: Callable[[str], int] | None = None,
    use_llmlingua: bool = True,
    llmlingua_model: str | None = None,
    min_keep_tokens: int = 16,
    keep_code_blocks: bool = True,
    keep_addresses: bool = True,
) -> list[dict[str, Any]]:
    """Compress retrieved *chunks* to roughly *target_tokens* total.

    Parameters
    ----------
    chunks:
        Chunk dicts from retrieval/rerank (``content`` or ``text``; score
        keys are preserved untouched).
    target_tokens:
        Approximate total token budget for the compressed context.
    query:
        Optional query forwarded to llmlingua's ``question``.
    count_tokens:
        Optional token counter; defaults to ``~4 chars/token`` heuristic.
    use_llmlingua:
        Prefer LLMLingua when installed (default True); extractive otherwise.
    llmlingua_model:
        Override the llmlingua HuggingFace model id.
    min_keep_tokens:
        Minimum tokens kept per chunk (default 16).
    keep_code_blocks:
        Keep fenced code blocks atomically (default True).
    keep_addresses:
        Keep addresses intact and score them as high-signal (default True).

    Returns
    -------
    list[dict]
        New chunk dicts (input not mutated) with compressed ``content``/``text``
        plus ``compression_method`` (``llmlingua`` / ``extractive`` /
        ``unchanged``), ``tokens_before``, ``tokens_after`` and
        ``compression_tags`` (``["code"]`` / ``["address"]`` when kept).
    """
    counter = count_tokens or _estimate_tokens
    target = max(1, int(target_tokens))

    pairs: list[tuple[int, str]] = []
    for idx, chunk in enumerate(chunks):
        text = _get_text(chunk)
        if text is not None:
            pairs.append((idx, text))

    result: list[dict[str, Any]] = [dict(c) for c in chunks]
    if not pairs:
        return result

    totals = [counter(text) for _, text in pairs]
    total = sum(totals)
    if total <= target:
        for idx, text in pairs:
            before = counter(text)
            _annotate(result[idx], method="unchanged", before=before, after=before)
        return result

    floor = max(1, min(int(min_keep_tokens), target // max(1, len(pairs))))
    budgets = _distribute_budget(totals, target, floor)
    use_llm = bool(use_llmlingua) and LLMLINGUA_AVAILABLE

    for (idx, text), budget in zip(pairs, budgets, strict=True):
        entry = result[idx]
        before = counter(text)
        if before <= budget:
            _annotate(entry, method="unchanged", before=before, after=before)
            continue

        out: str
        method: str
        stats: dict[str, Any] = {"kept_code_blocks": 0, "kept_addresses": 0}
        if use_llm:
            try:
                out = _llmlingua_compress(text, budget, query, llmlingua_model)
                method = "llmlingua"
            except Exception as exc:
                logger.debug(
                    "prompt_compression: llmlingua compress failed (%s) — extractive fallback",
                    exc,
                )
                out, method, stats = _extractive_compress(
                    text, budget, counter, keep_code_blocks, keep_addresses
                )
        else:
            out, method, stats = _extractive_compress(
                text, budget, counter, keep_code_blocks, keep_addresses
            )

        after = counter(out)
        _annotate(entry, method=method, before=before, after=after, stats=stats)
        _set_text(entry, out)

    return result


def layout_context(
    chunks: Sequence[dict[str, Any]],
    query: str,
    *,
    top_chunks: int = 3,
    score_key: str | None = None,
) -> list[str]:
    """Order prompt parts to defeat "lost in the middle".

    Returns ``[top_chunk_1, ..., top_chunk_N, middle_chunk_1, ..., query]``:
    highest-relevance chunks at the top of the window, lower-relevance in the
    middle, user query LAST. Missing or tied scores keep original (retrieval)
    order via a stable sort.

    Parameters
    ----------
    chunks:
        Chunk dicts (post-rerank preferred: ``rerank_score`` /
        ``combined_score`` / ``cross_score``; retrieval keys also supported).
    query:
        The user query — always placed last in the returned layout.
    top_chunks:
        How many highest-relevance chunks go to the top of the window.
    score_key:
        Force a specific score key; defaults to auto-detection.
    """
    top_n = max(1, int(top_chunks))
    ordered = sorted(chunks, key=lambda c: _chunk_score(c, score_key), reverse=True)
    top = ordered[:top_n]
    middle = ordered[top_n:]
    parts = [_get_text(c) or "" for c in top + middle]
    parts = [p for p in parts if p]
    if query:
        parts.append(query)
    return parts


def pack_context(
    chunks: Sequence[dict[str, Any]],
    query: str,
    *,
    top_chunks: int = 3,
    score_key: str | None = None,
    separator: str = "\n\n",
) -> str:
    """Assemble the final prompt with the query LAST (lost-in-the-middle safe).

    Intended to run AFTER ``compress_context``::

        prompt = pack_context(compress_context(results, 2048, query=query), query)
    """
    return separator.join(layout_context(chunks, query, top_chunks=top_chunks, score_key=score_key))
