"""Curated crypto-expert knowledge for the RugBrain chatbot.

Moved verbatim from ``scripts/crypto_expert_knowledge_data.py``; split so every module stays under 200
lines. Pure data: no I/O, no third-party imports.
"""

from __future__ import annotations

from typing import Any

from rugbrain.knowledge.crypto_expert import part_1 as _part_1
from rugbrain.knowledge.crypto_expert import part_2 as _part_2
from rugbrain.knowledge.crypto_expert import part_3 as _part_3
from rugbrain.knowledge.crypto_expert import part_4 as _part_4
from rugbrain.knowledge.crypto_expert import part_5 as _part_5
from rugbrain.knowledge.crypto_expert import part_6 as _part_6
from rugbrain.knowledge.crypto_expert import part_7 as _part_7
from rugbrain.knowledge.crypto_expert import part_8 as _part_8
from rugbrain.knowledge.crypto_expert import part_9 as _part_9

EXPERT_DOCS: list[dict[str, Any]] = [*_part_1.ITEMS, *_part_2.ITEMS, *_part_3.ITEMS, *_part_4.ITEMS, *_part_5.ITEMS, *_part_6.ITEMS, *_part_7.ITEMS, *_part_8.ITEMS, *_part_9.ITEMS]

def docs_for_collection(collection: str) -> list[dict[str, Any]]:
    """Return the curated docs routed to *collection*."""
    return [d for d in EXPERT_DOCS if d.get("collection", "education") == collection]

def collection_counts() -> dict[str, int]:
    """Return {collection: number_of_docs} for the curated set."""
    counts: dict[str, int] = {}
    for d in EXPERT_DOCS:
        c = d.get("collection", "education")
        counts[c] = counts.get(c, 0) + 1
    return counts

__all__ = ["EXPERT_DOCS", "collection_counts", "docs_for_collection"]
