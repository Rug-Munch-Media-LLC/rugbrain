"""Classify a question into a routing shape (pure, no I/O)."""

from __future__ import annotations

from typing import Any


def classify(question: str, table: dict[str, Any]) -> dict[str, Any]:
    """Return the best-matching shape; the documented default when nothing matches."""
    text = (question or "").lower()
    best: dict[str, Any] | None = None
    best_hits: list[str] = []
    for shape in table["shapes"]:
        hits = [kw for kw in shape["keywords"] if kw in text]
        if len(hits) > len(best_hits):
            best, best_hits = shape, hits
    if best is None:
        return {"shape": table.get("default_shape", "default"),
                "layer": table["default_layer"], "confidence": "default", "matched": []}
    return {"shape": best["id"], "layer": best["layer"],
            "confidence": "matched", "matched": best_hits}
