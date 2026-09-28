"""Offline retrieval backtest harness — result matching, percentiles, golden loading.

Split out of ``harness`` so the SCORING primitives (does this hit match the
gold?) are readable without the runner, the live-retriever wiring and the
aggregation. The runner itself is :mod:`rugbrain.eval.harness`; the direct
Qdrant retriever is :mod:`rugbrain.eval.qdrant_retriever`.

Contract carried by this module::

    load_golden(path) -> list[GoldenQuery]

``retriever`` is an async callable ``(query, collection, limit) -> list[dict]``
whose result dicts carry an id under ``id`` / ``doc_id`` / ``uuid`` /
``chunk_id`` (checked in that order) and optionally ``content``. Relevance is
judged by exact id match when the golden row carries ``relevant_doc_ids``,
otherwise by case-insensitive substring match of ``gold_chunks`` against
``content`` (Qdrant point ids are UUIDs and not stable across re-ingests).

[REVIEW] Golden-set provenance: rows are marked ``synthetic`` (hand-written,
not yet confirmed against the live corpus) or ``verified`` (the gold chunk was
observed in a real query result). Only ``verified`` rows should be used to
raise baselines; see ``docs/EVALS.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Default golden set shipped with the package.
DEFAULT_GOLDEN = Path(__file__).resolve().parent / "golden" / "crypto_rag_golden.jsonl"

# Result-dict id keys, in priority order (mirrors run_eval / retrieval_eval).
_ID_KEYS = ("id", "doc_id", "uuid", "chunk_id")

# Default rank cutoffs reported by the harness.
DEFAULT_K_VALUES: tuple[int, ...] = (5, 10)


class GoldenQuery(BaseModel):
    """One labeled (query, expected relevant chunks) row from the golden set."""

    model_config = ConfigDict(extra="ignore")

    query: str
    collection: str | None = None
    gold_chunks: list[str] = Field(default_factory=list)
    relevant_doc_ids: list[str] = Field(default_factory=list)
    expected_answer: str | None = None
    category: str = "general"
    provenance: str = "synthetic"
    provenance_note: str | None = None

    @property
    def has_gold(self) -> bool:
        """True when the row carries any ground-truth signal."""
        return bool(self.gold_chunks or self.relevant_doc_ids)


def result_id(item: dict[str, Any]) -> str:
    """Best-effort id extraction from a search result dict."""
    for key in _ID_KEYS:
        if key in item and item[key] not in (None, ""):
            return str(item[key])
    content = str(item.get("content", ""))
    return f"content:{hash(content) & 0xFFFFFFFF}"


def item_is_relevant(item: dict[str, Any], example: GoldenQuery) -> bool:
    """True when a retrieved result matches the golden ground truth.

    Exact doc-id match wins when ``relevant_doc_ids`` is present; otherwise a
    case-insensitive substring match of ``gold_chunks`` against ``content``.
    """
    if example.relevant_doc_ids and result_id(item) in example.relevant_doc_ids:
        return True
    if not example.gold_chunks:
        return False
    content = (item.get("content") or "").lower()
    return any(chunk.lower() in content for chunk in example.gold_chunks)


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile (pure python, no numpy)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(len(ordered) * p / 100.0)))
    return ordered[rank - 1]


def load_golden(path: Path | str = DEFAULT_GOLDEN) -> list[GoldenQuery]:
    """Parse a JSONL golden set (one object per line; ``#`` comments allowed)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"golden set not found: {path}")
    rows: list[GoldenQuery] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(GoldenQuery.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"invalid golden line {path}:{line_no}: {exc}") from exc
    if not rows:
        raise ValueError(f"golden set contains no examples: {path}")
    return rows


__all__ = [
    "DEFAULT_GOLDEN",
    "DEFAULT_K_VALUES",
    "GoldenQuery",
    "item_is_relevant",
    "load_golden",
    "percentile",
    "result_id",
]
