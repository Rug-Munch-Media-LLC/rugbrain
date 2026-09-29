"""Qdrant payload -> retriever candidate dicts.

Self-contained because reading the WRONG payload key is its own silent
degradation class: the retriever used to read only ``text``, which exists in
almost no real collection (the corpus stores ``content``), so every context
came back with an empty body and downstream judges scored real hits as
misses.
"""

from __future__ import annotations

from typing import Any

#: Payload fields that can carry a document's readable body, in precedence
#: order.
PAYLOAD_TEXT_KEYS: tuple[str, ...] = ("content", "text", "body", "chunk_text", "title")


def payload_text(payload: dict[str, Any]) -> str:
    """First non-empty readable field in a Qdrant payload."""
    for key in PAYLOAD_TEXT_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def to_candidates(points: Any) -> list[dict[str, Any]]:
    """Qdrant points -> retriever candidate dicts.

    BOTH ``text`` and ``content`` are populated deliberately: ``content`` is
    what the mission-eval judge and the rerank text extractor read, and
    ``text`` keeps the historical dual_tier contract for callers written
    against it. Reading only one of them is how real hits were scored as
    misses on an empty body.
    """
    out = []
    for p in points:
        payload = p.payload or {}
        body = payload_text(payload)
        out.append(
            {
                "text": body,
                "content": body,
                "meta": payload.get("metadata", {}),
                "id": str(p.id),
            }
        )
    return out
