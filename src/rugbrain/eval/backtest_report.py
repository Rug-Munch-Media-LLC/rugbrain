"""Per-query and aggregate result types for the offline backtest harness.

Split out of ``harness`` so the report SHAPE (what a run reports) is separate
from the runner that produces it (:mod:`rugbrain.eval.harness`). Keeping the
JSON projection next to the dataclasses is deliberate: the JSON is a CI
artifact and a smoke-script contract, and a hand-mirrored serializer drifts
from the dataclass the moment a field is added.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rugbrain.eval.golden_set import DEFAULT_GOLDEN, DEFAULT_K_VALUES, GoldenQuery

if TYPE_CHECKING:
    pass


@dataclass
class QueryScore:
    """Per-query retrieval score at every requested cutoff."""

    query: str
    collection: str | None = None
    retrieved_ids: list[str] = field(default_factory=list)
    relevant_ids: list[str] = field(default_factory=list)
    recall: dict[int, float] = field(default_factory=dict)
    ndcg: dict[int, float] = field(default_factory=dict)
    mrr_value: float = 0.0
    latency_ms: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "collection": self.collection,
            "recall": {str(k): round(v, 4) for k, v in self.recall.items()},
            "ndcg": {str(k): round(v, 4) for k, v in self.ndcg.items()},
            "mrr": round(self.mrr_value, 4),
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
        }


@dataclass
class BacktestReport:
    """Aggregate retrieval-quality report over a golden set."""

    total: int = 0
    errors: int = 0
    status: str = "ok"  # ok | unavailable | empty
    k_values: tuple[int, ...] = DEFAULT_K_VALUES
    mean_recall: dict[int, float] = field(default_factory=dict)
    mean_ndcg: dict[int, float] = field(default_factory=dict)
    mean_mrr: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    per_query: list[QueryScore] = field(default_factory=list)

    def metric(self, name: str, k: int) -> float:
        """Look up an aggregate metric by name (``recall`` / ``ndcg`` / ``mrr``)."""
        if name == "mrr":
            return self.mean_mrr
        if name == "recall":
            return self.mean_recall.get(k, 0.0)
        if name == "ndcg":
            return self.mean_ndcg.get(k, 0.0)
        raise KeyError(f"unknown metric {name!r}")

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable report (used by the smoke script + CI artifacts)."""
        return {
            "status": self.status,
            "total": self.total,
            "errors": self.errors,
            "k_values": list(self.k_values),
            "mean_recall": {str(k): round(v, 4) for k, v in self.mean_recall.items()},
            "mean_ndcg": {str(k): round(v, 4) for k, v in self.mean_ndcg.items()},
            "mean_mrr": round(self.mean_mrr, 4),
            "latency_ms": {
                "p50": round(self.p50_latency_ms, 1),
                "p95": round(self.p95_latency_ms, 1),
            },
            "per_query": [q.as_dict() for q in self.per_query],
        }


def report_to_json(report: BacktestReport, path: Path | str) -> None:
    """Write a report to JSON (creates parent dirs).

    Lives next to the dataclasses because the JSON projection is a CI
    artifact contract: keeping serializer and shape in one file is what stops
    them drifting apart when a field is added.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def run_backtest_sync(
    corpus: Path | str | Sequence[GoldenQuery] = DEFAULT_GOLDEN,
    retriever: Callable[..., Any] | None = None,
    top_k: int = 10,
    k_values: Iterable[int] = DEFAULT_K_VALUES,
    limit: int | None = None,
) -> BacktestReport:
    """Blocking wrapper around ``harness.run_backtest`` for scripts/CLI use."""
    from rugbrain.eval.harness import run_backtest

    return asyncio.run(run_backtest(corpus, retriever, top_k=top_k, k_values=k_values, limit=limit))


__all__ = ["BacktestReport", "QueryScore", "report_to_json", "run_backtest_sync"]
