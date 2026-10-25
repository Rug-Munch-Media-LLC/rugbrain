"""Cosine search-back over the JSONL URL corpus (proof of ingest, not a ranker)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector)) or 1.0


def search_url_corpus(query_vector: list[float], *, path: Path, top_k: int = 3) -> list[dict[str, Any]]:
    """Return the best *top_k* chunks by cosine similarity, with provenance."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    qnorm = _norm(query_vector)
    scored = []
    for row in rows:
        vector = row.get("vector") or []
        if len(vector) != len(query_vector):
            continue
        score = sum(a * b for a, b in zip(query_vector, vector)) / (qnorm * _norm(vector))
        scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {"score": round(score, 6), "url": row["url"], "text": row["text"][:160], "provenance": row["provenance"]}
        for score, row in scored[:top_k]
    ]
