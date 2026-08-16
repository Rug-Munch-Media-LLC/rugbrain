"""
Retrieval-only eval runner — hit@k / recall@k / precision@k + latency percentiles.

Measures how well the *live* RAG search path retrieves gold chunks for a
curated crypto golden set. Deliberately retrieval-only: it scores ranking
quality (``hit_rate_at_k``, ``recall_at_k``, ``precision_at_k``) and end-to-end
search latency (p50/p95) — it does NOT measure generation/faithfulness (see
``run_eval`` + ``metrics`` for the RAG-quality half of the harness).

Two runner modes:

* ``--endpoint http://localhost:8012`` (default): hits the live
  ``/api/v1/search`` HTTP endpoint with an ``X-API-Key`` header. This measures
  the real production path including auth, rate limiting, hybrid dense+BM25
  search and any reranker configured on the route.
* ``--inprocess``: calls ``rugbrain.search.engine.search_multi_collection``
  directly (no HTTP). Useful when no server is up; requires Redis/Qdrant to be
  reachable from the eval process.

Golden-set format (JSONL, one object per line)::

    {"query": "how does Uniswap V3 compute swap fees",
     "gold_chunks": ["Oracle Manipulation & TWAP Exploits", "TWAP"],
     "collection": "coding_knowledge",
     "expected_answer": "..."}

Relevance judgment: a retrieved result is *relevant* when its ``content``
contains any of the entry's ``gold_chunks`` as a case-insensitive substring.
Because Qdrant doc ids are UUIDs (not stable across ingests), content matching
is the honest ground-truth signal for a live corpus — the runner also accepts
``relevant_doc_ids`` in a golden line for datasets where ids ARE stable, and
falls back to exact id matching when present.

Latency is measured per query end-to-end (HTTP round-trip in endpoint mode,
engine call in inprocess mode) and reported as p50/p95 (plus mean).

Usage::

    python -m rugbrain.eval.retrieval_eval [--endpoint http://localhost:8012]
                                           [--top-k 5] [--json]

Read-only: issues GET /api/v1/search only; never writes to Qdrant/Redis.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rugbrain.eval.ndcg import hit_rate_at_k, precision_at_k, recall_at_k

# Default golden set shipped with the package (see golden_set.jsonl).
DEFAULT_GOLDEN_SET = Path(__file__).resolve().parent / "golden_set.jsonl"
DEFAULT_ENDPOINT = os.getenv("RAG_EVAL_ENDPOINT", "http://localhost:8012")
API_KEY_ENV = "RMI_RAG_API_KEY"

# Result-dict id keys, in priority order.
_ID_KEYS = ("id", "doc_id", "uuid", "chunk_id")


# ── golden-set model ─────────────────────────────────────────────────


class GoldenExample(BaseModel):
    """One labeled (query, gold chunk) pair from the golden set JSONL."""

    model_config = ConfigDict(extra="ignore")

    query: str
    gold_chunks: list[str] = Field(default_factory=list)
    collection: str | None = None
    expected_answer: str | None = None
    relevant_doc_ids: list[str] = Field(default_factory=list)

    @property
    def has_gold(self) -> bool:
        return bool(self.gold_chunks or self.relevant_doc_ids)


# ── per-query / aggregate results ────────────────────────────────────


@dataclass
class QueryEvalResult:
    query: str
    collection: str | None = None
    retrieved_ids: list[str] = field(default_factory=list)
    relevant_ids: list[str] = field(default_factory=list)
    hit_at_1: float = 0.0
    hit_at_k: float = 0.0
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class EvalReport:
    total: int = 0
    errors: int = 0
    mean_hit_at_1: float = 0.0
    mean_hit_at_k: float = 0.0
    mean_recall_at_k: float = 0.0
    mean_precision_at_k: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    mean_latency_ms: float = 0.0
    per_query: list[QueryEvalResult] = field(default_factory=list)
    k: int = 5


# ── result helpers ───────────────────────────────────────────────────


def _result_id(item: dict[str, Any]) -> str:
    """Best-effort doc id extraction from a search result dict."""
    for key in _ID_KEYS:
        if key in item and item[key] not in (None, ""):
            return str(item[key])
    # Fall back to a stable fingerprint of the content so content-matched
    # relevant sets still align with retrieved ids.
    content = str(item.get("content", ""))
    return f"content:{hash(content) & 0xFFFFFFFF}"


def _item_is_relevant(item: dict[str, Any], example: GoldenExample) -> bool:
    """True when a retrieved result matches the golden ground truth.

    Exact doc-id match wins when ``relevant_doc_ids`` is present; otherwise a
    case-insensitive substring match against ``gold_chunks`` on the result
    ``content``. A chunk match counts even when the doc id is a UUID that
    differs from the ingest-time id.
    """
    if example.relevant_doc_ids:
        rid = _result_id(item)
        if rid in example.relevant_doc_ids:
            return True
        # Fall through to content matching so UUID corpus ids still score.
    if not example.gold_chunks:
        return False
    content = (item.get("content") or "").lower()
    return any(chunk.lower() in content for chunk in example.gold_chunks)


# ── percentile helpers (pure python, no numpy) ───────────────────────


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile of *values* (pure python)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(len(ordered) * p / 100.0)))
    return ordered[rank - 1]


# ── search function contracts ────────────────────────────────────────

# A search fn is an async callable (query, limit, collection|None) -> list[dict].


async def search_via_endpoint(
    base_url: str,
    api_key: str,
    client: Any,
    query: str,
    limit: int,
    collection: str | None,
) -> list[dict[str, Any]]:
    """Call the live ``/api/v1/search`` route (read-only GET)."""
    params: dict[str, Any] = {"q": query, "limit": limit}
    if collection:
        params["collection"] = collection
    headers = {"X-API-Key": api_key} if api_key else {}
    resp = await client.get(f"{base_url}/api/v1/search", params=params, headers=headers)
    resp.raise_for_status()
    data = json.loads(resp.text, strict=False)
    return data.get("results") or []


async def search_inprocess(
    query: str,
    limit: int,
    collection: str | None,
) -> list[dict[str, Any]]:
    """Call ``rugbrain.search.engine.search_multi_collection`` directly."""
    from rugbrain.search.engine import search_multi_collection

    return await search_multi_collection(
        query,
        collections=[collection] if collection else None,
        limit=limit,
    )


# ── core runner ──────────────────────────────────────────────────────


async def run_query(
    search_fn: Callable[..., Any],
    example: GoldenExample,
    top_k: int,
) -> QueryEvalResult:
    """Run one golden query through *search_fn* and score the ranking.

    ``search_fn`` is awaited as ``search_fn(query, limit, collection)`` and must
    return a list of result dicts carrying ``content`` (for chunk matching) and
    ideally an id. Errors are captured into ``error`` so one bad query cannot
    abort the run.
    """
    result = QueryEvalResult(query=example.query, collection=example.collection)
    start = time.perf_counter()
    try:
        items = await search_fn(example.query, top_k, example.collection)
        result.retrieved_ids = [_result_id(item) for item in (items or [])]
        result.latency_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # pragma: no cover - defensive
        result.error = f"{type(exc).__name__}: {exc}"
        result.latency_ms = (time.perf_counter() - start) * 1000.0
        return result

    if example.gold_chunks:
        # Chunk-mode: the gold chunks ARE the ground-truth items. A chunk is
        # "recalled" when it appears in at least one of the top-k docs.
        #   hit@k   = any gold chunk surfaced in top-k
        #   recall@k = fraction of gold chunks surfaced in top-k
        #   precision@k = fraction of top-k docs containing >= 1 gold chunk
        top_items = (items or [])[:top_k]
        matched: set[str] = set()
        relevant_docs = 0
        for item in top_items:
            if _item_is_relevant(item, example):
                relevant_docs += 1
                for chunk in example.gold_chunks:
                    if chunk.lower() in (item.get("content") or "").lower():
                        matched.add(chunk)
        result.relevant_ids = sorted(example.gold_chunks)
        top1 = (items or [None])[0]
        result.hit_at_1 = 1.0 if top1 is not None and _item_is_relevant(top1, example) else 0.0
        result.hit_at_k = 1.0 if matched else 0.0
        result.recall_at_k = len(matched) / len(example.gold_chunks) if example.gold_chunks else 0.0
        result.precision_at_k = relevant_docs / top_k if top_k > 0 else 0.0
        return result

    # Doc-id mode: standard retrieval metrics against labeled relevant ids.
    relevant_set = set(example.relevant_doc_ids)
    result.relevant_ids = sorted(relevant_set)

    if not relevant_set:
        # No labeled ground truth at all: nothing to score.
        result.hit_at_1 = 0.0
        result.hit_at_k = 0.0
        result.recall_at_k = 0.0
        result.precision_at_k = 0.0
        return result

    result.hit_at_1 = hit_rate_at_k(result.retrieved_ids, relevant_set, k=1)
    result.hit_at_k = hit_rate_at_k(result.retrieved_ids, relevant_set, k=top_k)
    result.recall_at_k = recall_at_k(result.retrieved_ids, relevant_set, k=top_k)
    result.precision_at_k = precision_at_k(result.retrieved_ids, relevant_set, k=top_k)
    return result


async def run_eval(
    search_fn: Callable[..., Any],
    examples: list[GoldenExample],
    top_k: int,
) -> EvalReport:
    """Run *examples* through *search_fn* sequentially and aggregate metrics."""
    per_query = [await run_query(search_fn, ex, top_k) for ex in examples]
    valid = [r for r in per_query if not r.error]
    report = EvalReport(
        total=len(examples), errors=len(per_query) - len(valid), per_query=per_query, k=top_k
    )

    if valid:
        report.mean_hit_at_1 = sum(r.hit_at_1 for r in valid) / len(valid)
        report.mean_hit_at_k = sum(r.hit_at_k for r in valid) / len(valid)
        report.mean_recall_at_k = sum(r.recall_at_k for r in valid) / len(valid)
        report.mean_precision_at_k = sum(r.precision_at_k for r in valid) / len(valid)
        latencies = [r.latency_ms for r in valid if r.latency_ms > 0]
        if latencies:
            report.p50_latency_ms = percentile(latencies, 50)
            report.p95_latency_ms = percentile(latencies, 95)
            report.mean_latency_ms = sum(latencies) / len(latencies)
    return report


# ── dataset loading ──────────────────────────────────────────────────


def load_golden_set(path: Path | str) -> list[GoldenExample]:
    """Parse a JSONL golden set (one object per line)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"golden set not found: {path}")
    examples: list[GoldenExample] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                examples.append(GoldenExample.model_validate_json(line))
            except Exception as exc:
                raise ValueError(f"invalid golden line {path}:{line_no}: {exc}") from exc
    if not examples:
        raise ValueError(f"golden set contains no examples: {path}")
    return examples


# ── CLI ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rugbrain.eval.retrieval_eval",
        description="Retrieval-only eval — hit@k / recall@k / precision@k + p50/p95 latency",
    )
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=DEFAULT_GOLDEN_SET,
        help=f"JSONL golden set (default: {DEFAULT_GOLDEN_SET.name})",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="rank cutoff k for hit@k / recall@k / precision@k (default: 5)",
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"live RAG search endpoint base URL (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="X-API-Key for the endpoint (default: $RMI_RAG_API_KEY)",
    )
    parser.add_argument(
        "--inprocess",
        action="store_true",
        help="call rugbrain.search.engine directly instead of the HTTP endpoint",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="per-query HTTP timeout in seconds (default: 180)",
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
        f"  {r.query[:58]:<60} hit1={r.hit_at_1:.2f} hitk={r.hit_at_k:.2f} "
        f"recall={r.recall_at_k:.2f} prec={r.precision_at_k:.2f} "
        f"lat={r.latency_ms:.0f}ms{error}"
    )


def _report_to_dict(report: EvalReport) -> dict[str, Any]:
    return {
        "k": report.k,
        "total_queries": report.total,
        "errors": report.errors,
        "hit_at_1": round(report.mean_hit_at_1, 4),
        "hit_at_k": round(report.mean_hit_at_k, 4),
        "recall_at_k": round(report.mean_recall_at_k, 4),
        "precision_at_k": round(report.mean_precision_at_k, 4),
        "latency_ms": {
            "p50": round(report.p50_latency_ms, 1),
            "p95": round(report.p95_latency_ms, 1),
            "mean": round(report.mean_latency_ms, 1),
        },
        "per_query": [
            {
                "query": r.query,
                "collection": r.collection,
                "hit_at_1": round(r.hit_at_1, 4),
                "hit_at_k": round(r.hit_at_k, 4),
                "recall_at_k": round(r.recall_at_k, 4),
                "precision_at_k": round(r.precision_at_k, 4),
                "latency_ms": round(r.latency_ms, 1),
                "error": r.error,
            }
            for r in report.per_query
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns process exit code (0 = success)."""
    args = build_parser().parse_args(argv)
    if args.top_k < 1:
        print(f"error: --top-k must be >= 1, got {args.top_k}", file=sys.stderr)
        return 2

    try:
        examples = load_golden_set(args.golden_set)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.inprocess:
        search_fn: Callable[..., Any] = search_inprocess
    else:
        api_key = args.api_key or os.getenv(API_KEY_ENV, "")
        if not api_key:
            print(
                f"error: no API key — set {API_KEY_ENV} or pass --api-key (endpoint mode)",
                file=sys.stderr,
            )
            return 2

        async def endpoint_search(
            query: str,
            limit: int,
            collection: str | None,
        ) -> list[dict[str, Any]]:
            import httpx

            async with httpx.AsyncClient(timeout=args.timeout) as client:
                return await search_via_endpoint(
                    args.endpoint, api_key, client, query, limit, collection
                )

        search_fn = endpoint_search

    report = asyncio.run(run_eval(search_fn, examples, top_k=args.top_k))

    if args.json:
        print(json.dumps(_report_to_dict(report), indent=2))
        return 1 if report.errors == report.total else 0

    print(
        f"rugbrain retrieval eval — {report.total} queries, top-{report.k}, "
        f"{'inprocess' if args.inprocess else args.endpoint}"
    )
    print(f"golden set: {args.golden_set}")
    for row in report.per_query:
        print(_format_row(row))
    print("-" * 76)
    print(
        f"mean hit@1={report.mean_hit_at_1:.4f}  hit@{report.k}={report.mean_hit_at_k:.4f}  "
        f"recall@{report.k}={report.mean_recall_at_k:.4f}  "
        f"precision@{report.k}={report.mean_precision_at_k:.4f}  "
        f"errors={report.errors}"
    )
    print(
        f"latency (ms): p50={report.p50_latency_ms:.0f}  p95={report.p95_latency_ms:.0f}  "
        f"mean={report.mean_latency_ms:.0f}"
    )
    return 1 if report.errors == report.total else 0


if __name__ == "__main__":
    raise SystemExit(main())
