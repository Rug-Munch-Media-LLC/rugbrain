"""Chunk-level budgets, text accessors, annotation and score lookup.

Moved verbatim from ``prompt_compression.py`` — the plumbing shared by
compression and the "lost in the middle" layout.
"""

from __future__ import annotations

from typing import Any


def _distribute_budget(
    token_counts: list[int],
    target_total: int,
    floor: int,
) -> list[int]:
    """Allocate per-chunk budgets proportional to original size, fitting the total.

    Every chunk keeps at least ``floor`` tokens; budgets are clamped to the
    chunk's original size and scaled down until the sum fits *target_total*.
    """
    n = len(token_counts)
    total = sum(token_counts)
    if n == 0 or total <= 0:
        return []

    scale = 1.0
    while True:
        budgets = [max(floor, min(t, int(t * target_total / total * scale))) for t in token_counts]
        if sum(budgets) <= target_total or scale < 0.05:
            return budgets
        scale *= 0.9


def _get_text(chunk: dict[str, Any]) -> str | None:
    """Chunk text under ``content`` or ``text`` (matching search/rerank lanes)."""
    for key in ("content", "text"):
        val = chunk.get(key)
        if isinstance(val, str):
            return val
    return None


def _set_text(chunk: dict[str, Any], value: str) -> None:
    """Write compressed text back to the field the chunk originally used."""
    if "content" in chunk:
        chunk["content"] = value
    elif "text" in chunk:
        chunk["text"] = value
    else:
        chunk["content"] = value


def _annotate(
    entry: dict[str, Any],
    *,
    method: str,
    before: int,
    after: int,
    stats: dict[str, Any] | None = None,
) -> None:
    """Tag a result chunk with compression metadata (``compression_*`` keys)."""
    stats = stats or {}
    tags: list[str] = []
    if stats.get("kept_code_blocks"):
        tags.append("code")
    if stats.get("kept_addresses"):
        tags.append("address")
    entry["compressed"] = method != "unchanged"
    entry["compression_method"] = method
    entry["tokens_before"] = before
    entry["tokens_after"] = after
    entry["compression_kept_code_blocks"] = stats.get("kept_code_blocks", 0)
    entry["compression_kept_addresses"] = stats.get("kept_addresses", 0)
    entry["compression_tags"] = tags


_SCORE_KEYS = (
    "rerank_score",
    "combined_score",
    "cross_score",
    "score",
    "rrf_score",
    "similarity",
    "bm25_score",
    "text_score",
)


def _chunk_score(chunk: dict[str, Any], score_key: str | None = None) -> float:
    """Best available relevance score (rerank first, retrieval after)."""
    keys = (score_key,) if score_key else _SCORE_KEYS
    for key in keys:
        val = chunk.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    meta = chunk.get("metadata") or {}
    for key in keys:
        val = meta.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0
