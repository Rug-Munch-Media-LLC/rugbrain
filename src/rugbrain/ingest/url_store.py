"""Append-only JSONL corpus store carrying provenance per chunk.

Vectors are written alongside text so search-back needs no service; a Qdrant
adapter can consume the same records (same shape, `vector` + `provenance`).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def corpus_path() -> Path:
    return Path(os.environ.get("RUGBRAIN_URL_CORPUS", "data/url_corpus.jsonl"))


async def store_records(records: list[dict[str, Any]], path: Path | None = None) -> int:
    """Append *records* and return how many were written."""
    target = path or corpus_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)
