"""Ordering, through the news channel's own reranker.

The news channel ranks with :func:`rugbrain.content_rerank.rerank_stories` —
local Qwen3-Reranker-8B first, hosted chain second, retrieval order last, with
the lane named on every story. Content from Substack, Medium, paragraph and
YouTube must be judged by that same head to be comparable on one page, so this
module is a thin call, not a new scorer.
"""

from __future__ import annotations

from typing import Any

from rugbrain.content_rerank import EDITORIAL_INTENT, rerank_stories

__all__ = ["rank_items"]


async def rank_items(
    items: list[dict[str, Any]],
    *,
    intent: str = EDITORIAL_INTENT,
    keep: int | None = None,
) -> list[dict[str, Any]]:
    """Rerank canonical items with the news channel's editorial intent."""
    if not items:
        return []
    return await rerank_stories(items, intent=intent, keep=keep or len(items))
