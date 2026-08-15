"""
Labeled retrieval eval CLI — measure NDCG@k / recall@k over a search function.

Usage::

    python -m rugbrain.eval.run_eval --dataset eval/sample_dataset.jsonl --top-k 5

Runs every query in a JSONL dataset through a search function and reports
ranking quality against the labeled ``relevant_doc_ids``:

* per-query: NDCG@k, recall@k, hit_rate@k, MRR
* aggregate: mean NDCG@k, mean recall@k, mean hit_rate@k, mean MRR
* optional answer-quality metrics (faithfulness / answer relevance) when the
  dataset lines carry an ``expected_answer``.

Dataset format (JSONL, one object per line)::

    {"query": "How are cross-chain bridge exploits prevented?",
     "relevant_doc_ids": ["bridge_security_prevention", "cross_chain_audit"],
     "expected_answer": "...", "collection": "threat_intel"}

Search function contract: an async callable ``(query: str, limit: int) ->
list[dict]`` whose result dicts carry an id under ``id`` / ``doc_id`` /
``uuid`` / ``chunk_id`` (checked in that order). Point ``--search`` at a
dotted path; the default is the live hybrid engine
``rugbrain.search_service.search_multi_collection`` which requires the
Qdrant/Redis backends — pass a stub for offline runs.

The CLI is intentionally thin: metric math lives in ``rugbrain.eval.ndcg`` /
``rugbrain.eval.metrics`` and is unit-tested there.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rugbrain.eval.metrics import answer_relevance, faithfulness
from rugbrain.eval.ndcg import hit_rate_at_k, mrr, ndcg_at_k, recall_at_k

# Default dataset shipped with the package (see sample_dataset.jsonl).
DEFAULT_DATASET = Path(__file__).resolve().parent / "sample_dataset.jsonl"
DEFAULT_SEARCH = "rugbrain.search_service.search_multi_collection"

# Result-dict id keys, in priority order.
_ID_KEYS = ("id", "doc_id", "uuid", "chunk_id")


# ── dataset model ────────────────────────────────────────────────────


class QueryExample(BaseModel):
    """One labeled (query, relevant ids) pair from the dataset JSONL."""

    model_config = ConfigDict(extra="ignore")

    query: str
    relevant_doc_ids: list[str] = Field(default_factory=list)
    expected_answer: str | None = None
    collection: str | None = None


# ── eval result models ───────────────────────────────────────────────


@dataclass
class QueryEvalResult:
    query: str
    retrieved_ids: list[str] = field(default_factory=list)
    relevant_ids: list[str] = field(default_factory=list)
    ndcg: float = 0.0
    recall: float = 0.0
    hit_rate: float = 0.0
    mrr_value: float = 0.0
    faithfulness_score: float | None = None
    answer_relevance_score: float | None = None
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class EvalSummary:
    total: int = 0
    errors: int = 0
    mean_ndcg: float = 0.0
    mean_recall: float = 0.0
    mean_hit_rate: float = 0.0
    mean_mrr: float = 0.0
    mean_faithfulness: float | None = None
    mean_answer_relevance: float | None = None
    per_query: list[QueryEvalResult] = field(default_factory=list)


# ── result-id extraction ─────────────────────────────────────────────


def _result_id(item: dict[str, Any]) -> str:
    """Best-effort doc id extraction from a search result dict."""
    for key in _ID_KEYS:
        if key in item and item[key] not in (None, ""):
            return str(item[key])
    raise ValueError(f"search result dict has no id key in {_ID_KEYS}: {item!r}")


# ── search function loading ──────────────────────────────────────────


def load_search_function(dotted_path: str) -> Callable[..., Any]:
    """
    Import an async search function from a dotted path, e.g.
    ``rugbrain.search_service.search_multi_collection``.
    """
    module_name, _, attr = dotted_path.rpartition(".")
    if not module_name or not attr:
        raise ValueError(f"invalid --search path {dotted_path!r}; expected module.attr")
    module = importlib.import_module(module_name)
    fn: Callable[..., Any] = getattr(module, attr)
    if not callable(fn):
        raise TypeError(f"{dotted_path!r} is not callable")
    return fn


# ── core runner ──────────────────────────────────────────────────────


async def run_query(
    search_fn: Callable[..., Any],
    example: QueryExample,
    top_k: int,
) -> QueryEvalResult:
    """
    Run one labeled query through *search_fn* and score the ranking.

    ``search_fn`` is awaited as ``search_fn(query, limit=top_k)`` and must
    return a list of dicts carrying a doc id (see ``_result_id``). Any
    exception is captured into ``error`` so one bad query cannot abort the
    whole run (mirrors the existing evaluation.py resilience).
    """
    result = QueryEvalResult(query=example.query, relevant_ids=list(example.relevant_doc_ids))
    start = time.perf_counter()
    try:
        items = await search_fn(example.query, limit=top_k)
        result.retrieved_ids = [_result_id(item) for item in (items or [])]
        result.latency_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # pragma: no cover - defensive, mirrors evaluation.py
        result.error = f"{type(exc).__name__}: {exc}"
        result.latency_ms = (time.perf_counter() - start) * 1000.0
        return result

    result.ndcg = ndcg_at_k(result.retrieved_ids, result.relevant_ids, k=top_k)
    result.recall = recall_at_k(result.retrieved_ids, result.relevant_ids, k=top_k)
    result.hit_rate = hit_rate_at_k(result.retrieved_ids, result.relevant_ids, k=top_k)
    result.mrr_value = mrr(result.retrieved_ids, result.relevant_ids)

    if example.expected_answer:
        # Faithfulness needs retrieved *content*, which the ranking metrics do
        # not use. Score it against whatever content the search function
        # returns; when results carry no content the metrics are skipped
        # (mean_faithfulness stays None in the summary).
        contexts = [str(item.get("content", "")) for item in (items or []) if item.get("content")]
        if contexts:
            result.faithfulness_score = faithfulness(example.expected_answer, contexts)
            result.answer_relevance_score = answer_relevance(example.expected_answer, example.query)
    return result


async def run_eval(
    search_fn: Callable[..., Any],
    examples: list[QueryExample],
    top_k: int,
) -> EvalSummary:
    """Run *examples* through *search_fn* sequentially and aggregate metrics."""
    per_query = [await run_query(search_fn, ex, top_k) for ex in examples]
    valid = [r for r in per_query if not r.error]
    summary = EvalSummary(
        total=len(examples), errors=len(per_query) - len(valid), per_query=per_query
    )

    if valid:
        summary.mean_ndcg = sum(r.ndcg for r in valid) / len(valid)
        summary.mean_recall = sum(r.recall for r in valid) / len(valid)
        summary.mean_hit_rate = sum(r.hit_rate for r in valid) / len(valid)
        summary.mean_mrr = sum(r.mrr_value for r in valid) / len(valid)

    scored = [r for r in valid if r.faithfulness_score is not None]
    if scored:
        summary.mean_faithfulness = sum(r.faithfulness_score or 0.0 for r in scored) / len(scored)
        summary.mean_answer_relevance = sum(r.answer_relevance_score or 0.0 for r in scored) / len(
            scored
        )
    return summary


# ── dataset loading ──────────────────────────────────────────────────


def load_dataset(path: Path | str) -> list[QueryExample]:
    """Parse a JSONL dataset of labeled examples (one object per line)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    examples: list[QueryExample] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                examples.append(QueryExample.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"invalid dataset line {path}:{line_no}: {exc}") from exc
    if not examples:
        raise ValueError(f"dataset contains no examples: {path}")
    return examples


# ── CLI ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rugbrain.eval.run_eval",
        description="Labeled retrieval eval — NDCG@k / recall@k over a search function",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"JSONL dataset of labeled queries (default: {DEFAULT_DATASET.name})",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="rank cutoff k for NDCG@k / recall@k (default: 5)",
    )
    parser.add_argument(
        "--search",
        default=DEFAULT_SEARCH,
        help=f"dotted path to async search fn (query, limit) -> list[dict] "
        f"(default: {DEFAULT_SEARCH})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the human table",
    )
    return parser


def _format_row(r: QueryEvalResult) -> str:
    error = f" [ERROR: {r.error}]" if r.error else ""
    return (
        f"  {r.query[:60]:<62} ndcg={r.ndcg:.4f} recall={r.recall:.4f} "
        f"hit={r.hit_rate:.4f} mrr={r.mrr_value:.4f}{error}"
    )


def _summary_to_dict(summary: EvalSummary) -> dict[str, Any]:
    return {
        "total_queries": summary.total,
        "errors": summary.errors,
        "mean_ndcg": round(summary.mean_ndcg, 4),
        "mean_recall": round(summary.mean_recall, 4),
        "mean_hit_rate": round(summary.mean_hit_rate, 4),
        "mean_mrr": round(summary.mean_mrr, 4),
        "mean_faithfulness": (
            round(summary.mean_faithfulness, 4) if summary.mean_faithfulness is not None else None
        ),
        "mean_answer_relevance": (
            round(summary.mean_answer_relevance, 4)
            if summary.mean_answer_relevance is not None
            else None
        ),
        "per_query": [
            {
                "query": r.query,
                "ndcg": round(r.ndcg, 4),
                "recall": round(r.recall, 4),
                "hit_rate": round(r.hit_rate, 4),
                "mrr": round(r.mrr_value, 4),
                "error": r.error,
            }
            for r in summary.per_query
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns process exit code (0 = success)."""
    args = build_parser().parse_args(argv)
    if args.top_k < 1:
        print(f"error: --top-k must be >= 1, got {args.top_k}", file=sys.stderr)
        return 2

    try:
        examples = load_dataset(args.dataset)
        search_fn = load_search_function(args.search)
    except (FileNotFoundError, ValueError, TypeError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = asyncio.run(run_eval(search_fn, examples, top_k=args.top_k))

    if args.json:
        print(json.dumps(_summary_to_dict(summary), indent=2))
        return 1 if summary.errors == summary.total else 0

    print(f"rugbrain eval — {summary.total} queries, top-{args.top_k}")
    print(f"search: {args.search}")
    print(f"dataset: {args.dataset}")
    for row in summary.per_query:
        print(_format_row(row))
    print("-" * 72)
    print(
        f"mean ndcg@{args.top_k}={summary.mean_ndcg:.4f}  "
        f"recall@{args.top_k}={summary.mean_recall:.4f}  "
        f"hit_rate@{args.top_k}={summary.mean_hit_rate:.4f}  "
        f"mrr={summary.mean_mrr:.4f}  "
        f"errors={summary.errors}"
    )
    return 1 if summary.errors == summary.total else 0


if __name__ == "__main__":
    raise SystemExit(main())
