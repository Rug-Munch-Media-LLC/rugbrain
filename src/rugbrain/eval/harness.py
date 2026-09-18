"""
Offline retrieval-quality backtest harness — recall@k / MRR / nDCG@k + latency.

This is the CI-callable sibling of ``retrieval_eval`` (live endpoint) and
``run_eval`` (labeled doc-id CLI). It is deliberately *offline-first*: the
retriever is injected, so the same harness runs against a recorded fixture in
PR CI and against live Qdrant in the nightly job. No LLM-judge dependency is
required — Ragas/DeepEval are optional extras (``requirements-eval.txt``) and
the harness degrades gracefully when they are absent.

Contract::

    run_backtest(corpus, retriever, top_k=10, k_values=(5, 10)) -> BacktestReport

``retriever`` is an async callable ``(query, collection, limit) -> list[dict]``
whose result dicts carry an id under ``id`` / ``doc_id`` / ``uuid`` /
``chunk_id`` (checked in that order) and optionally ``content``. Relevance is
judged by exact id match when the golden row carries ``relevant_doc_ids``,
otherwise by case-insensitive substring match of ``gold_chunks`` against
``content`` (Qdrant point ids are UUIDs and not stable across re-ingests).

Fail-open: when the retriever raises for every query the report is returned
with ``status="unavailable"`` and ``errors == total`` — the caller (gates /
smoke script) decides whether that is a skip or a failure. The harness never
crashes on a missing backend.

[REVIEW] Golden-set provenance: rows are marked ``synthetic`` (hand-written,
not yet confirmed against the live corpus) or ``verified`` (the gold chunk was
observed in a real query result). Only ``verified`` rows should be used to
raise baselines; see ``docs/EVALS.md``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rugbrain.eval.ndcg import (
    mrr,
    mrr_from_gains,
    ndcg_at_k,
    ndcg_from_gains,
    recall_at_k,
)

# Default golden set shipped with the package.
DEFAULT_GOLDEN = Path(__file__).resolve().parent / "golden" / "crypto_rag_golden.jsonl"

# Result-dict id keys, in priority order (mirrors run_eval / retrieval_eval).
_ID_KEYS = ("id", "doc_id", "uuid", "chunk_id")

# Default rank cutoffs reported by the harness.
DEFAULT_K_VALUES: tuple[int, ...] = (5, 10)


# ── golden-set model ─────────────────────────────────────────────────


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

    @property
    def has_gold(self) -> bool:
        """True when the row carries any ground-truth signal."""
        return bool(self.gold_chunks or self.relevant_doc_ids)


# ── per-query / aggregate results ────────────────────────────────────


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
            "per_query": [
                {
                    "query": q.query,
                    "collection": q.collection,
                    "recall": {str(k): round(v, 4) for k, v in q.recall.items()},
                    "ndcg": {str(k): round(v, 4) for k, v in q.ndcg.items()},
                    "mrr": round(q.mrr_value, 4),
                    "latency_ms": round(q.latency_ms, 1),
                    "error": q.error,
                }
                for q in self.per_query
            ],
        }


# ── result helpers ───────────────────────────────────────────────────


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


# ── golden-set loading ───────────────────────────────────────────────


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


# ── core runner ──────────────────────────────────────────────────────


async def score_query(
    retriever: Callable[..., Any],
    example: GoldenQuery,
    top_k: int,
    k_values: Iterable[int],
) -> QueryScore:
    """Run one golden query through *retriever* and score the ranking.

    ``retriever`` is awaited as ``retriever(query, collection, limit)``. Any
    exception is captured into ``error`` so one bad query cannot abort the run.
    """
    score = QueryScore(query=example.query, collection=example.collection)
    start = time.perf_counter()
    try:
        items = await retriever(example.query, example.collection, top_k)
        score.retrieved_ids = [result_id(item) for item in (items or [])]
        score.latency_ms = (time.perf_counter() - start) * 1000.0
    except Exception as exc:  # pragma: no cover - defensive, fail-open
        score.error = f"{type(exc).__name__}: {exc}"
        score.latency_ms = (time.perf_counter() - start) * 1000.0
        return score

    if example.relevant_doc_ids:
        # Doc-id mode: standard retrieval metrics against labeled ids.
        relevant: list[str] = list(example.relevant_doc_ids)
        score.relevant_ids = relevant
        for k in k_values:
            score.recall[k] = recall_at_k(score.retrieved_ids, relevant, k=k)
            score.ndcg[k] = ndcg_at_k(score.retrieved_ids, relevant, k=k)
        score.mrr_value = mrr(score.retrieved_ids, relevant)
        return score

    # Chunk mode: relevance is judged per retrieved rank by content match.
    #   recall@k = fraction of gold chunks surfaced in the top-k
    #   nDCG@k / MRR = ranking quality over binary per-rank gains
    top_items = (items or [])[:top_k]
    matched: set[str] = set()
    for item in top_items:
        content = (item.get("content") or "").lower()
        for chunk in example.gold_chunks:
            if chunk.lower() in content:
                matched.add(chunk)
    score.relevant_ids = sorted(matched)
    gains = [1.0 if item_is_relevant(item, example) else 0.0 for item in top_items]
    for k in k_values:
        score.recall[k] = (
            len(
                {
                    c
                    for c in example.gold_chunks
                    if any(
                        c.lower() in (item.get("content") or "").lower() for item in top_items[:k]
                    )
                }
            )
            / len(example.gold_chunks)
            if example.gold_chunks
            else 0.0
        )
        score.ndcg[k] = ndcg_from_gains(gains, k=k)
    score.mrr_value = mrr_from_gains(gains)
    return score


async def run_backtest(
    corpus: Path | str | Sequence[GoldenQuery] = DEFAULT_GOLDEN,
    retriever: Callable[..., Any] | None = None,
    top_k: int = 10,
    k_values: Iterable[int] = DEFAULT_K_VALUES,
    limit: int | None = None,
) -> BacktestReport:
    """
    Run a golden set through *retriever* and aggregate retrieval metrics.

    Args:
        corpus: path to a JSONL golden set, or an already-loaded sequence.
        retriever: async ``(query, collection, limit) -> list[dict]``. When
            ``None`` the live in-process engine is used (requires Qdrant).
        top_k: number of results to request per query.
        k_values: rank cutoffs to report (default 5 and 10).
        limit: optional cap on the number of golden rows (smoke subset).

    Returns:
        A ``BacktestReport``. ``status`` is ``"unavailable"`` when every query
        errored (backend down), ``"empty"`` when the corpus has no rows, else
        ``"ok"``. Never raises for a missing backend.
    """
    k_tuple = tuple(sorted(set(k_values)))
    examples = load_golden(corpus) if isinstance(corpus, (str, Path)) else list(corpus)
    if limit is not None:
        examples = examples[:limit]

    report = BacktestReport(total=len(examples), k_values=k_tuple)
    if not examples:
        report.status = "empty"
        return report

    if retriever is None:
        retriever = _live_retriever()

    per_query = [await score_query(retriever, ex, top_k, k_tuple) for ex in examples]
    report.per_query = per_query
    valid = [q for q in per_query if not q.error]
    report.errors = len(per_query) - len(valid)

    if not valid:
        report.status = "unavailable"
        return report

    for k in k_tuple:
        report.mean_recall[k] = sum(q.recall.get(k, 0.0) for q in valid) / len(valid)
        report.mean_ndcg[k] = sum(q.ndcg.get(k, 0.0) for q in valid) / len(valid)
    report.mean_mrr = sum(q.mrr_value for q in valid) / len(valid)

    latencies = [q.latency_ms for q in valid if q.latency_ms > 0]
    if latencies:
        report.p50_latency_ms = percentile(latencies, 50)
        report.p95_latency_ms = percentile(latencies, 95)
    return report


def _live_retriever() -> Callable[..., Any]:
    """Build the default live retriever (in-process search engine)."""

    async def live(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        from rugbrain.search.engine import search_multi_collection

        return await search_multi_collection(
            query,
            collections=[collection] if collection else None,
            limit=limit,
            rerank=False,
        )

    return live


def direct_qdrant_retriever(
    qdrant_url: str | None = None,
    embedder: Any | None = None,
) -> Callable[..., Any]:
    """
    Build an offline retriever that queries Qdrant directly (no Redis/FAISS).

    The production engine depends on a Redis doc-index and a built FAISS index;
    when either is absent it returns zero results. This retriever bypasses both
    by scrolling the collection's stored vectors and scoring cosine similarity
    in-process — enough to exercise the harness end-to-end against a live
    Qdrant without the full search stack.

    Args:
        qdrant_url: Qdrant base URL (default ``$QDRANT_URL`` or localhost:6334).
        embedder: object with ``async embed_query(str) -> list[float]``; when
            ``None`` the rugbrain embedder is loaded lazily.

    Returns:
        Async ``(query, collection, limit) -> list[dict]`` with ``id`` and
        ``content`` keys, sorted by descending cosine similarity.
    """
    import os
    import urllib.request

    base = (qdrant_url or os.getenv("QDRANT_URL") or "http://127.0.0.1:6334").rstrip("/")

    async def retrieve(query: str, collection: str | None, limit: int) -> list[dict[str, Any]]:
        if not collection:
            return []
        emb = embedder
        if emb is None:
            from rugbrain.search.engine import get_embedder  # type: ignore[attr-defined]

            emb = await get_embedder()
        q_vec = await emb.embed_query(query)

        req = urllib.request.Request(  # noqa: S310 - local Qdrant URL
            f"{base}/collections/{collection}/points/scroll",
            data=json.dumps({"limit": 2000, "with_payload": True, "with_vector": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        # Blocking HTTP is acceptable here: the eval harness is a batch tool,
        # not a request-serving path (ASYNC210 is a false positive for evals).
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310, ASYNC210
            data = json.load(resp)
        points = data.get("result", {}).get("points", [])

        scored: list[tuple[float, dict[str, Any]]] = []
        for point in points:
            vec = point.get("vector")
            if isinstance(vec, dict):
                vec = next(iter(vec.values()), None)
            if not vec:
                continue
            sim = _cosine(q_vec, vec)
            payload = point.get("payload", {})
            content = " ".join(
                str(payload.get(key) or "")
                for key in ("content", "title", "description", "code_block", "ocr_text")
            )
            scored.append(
                (
                    sim,
                    {
                        "id": str(point.get("id")),
                        "content": content,
                        "collection": collection,
                        "score": sim,
                    },
                )
            )
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    return retrieve


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity with a zero-vector guard."""
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def run_backtest_sync(
    corpus: Path | str | Sequence[GoldenQuery] = DEFAULT_GOLDEN,
    retriever: Callable[..., Any] | None = None,
    top_k: int = 10,
    k_values: Iterable[int] = DEFAULT_K_VALUES,
    limit: int | None = None,
) -> BacktestReport:
    """Blocking wrapper around :func:`run_backtest` for scripts/CLI use."""
    return asyncio.run(run_backtest(corpus, retriever, top_k=top_k, k_values=k_values, limit=limit))


def report_to_json(report: BacktestReport, path: Path | str) -> None:
    """Write a report to JSON (creates parent dirs)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


__all__ = [
    "DEFAULT_GOLDEN",
    "DEFAULT_K_VALUES",
    "BacktestReport",
    "GoldenQuery",
    "QueryScore",
    "direct_qdrant_retriever",
    "item_is_relevant",
    "load_golden",
    "percentile",
    "report_to_json",
    "result_id",
    "run_backtest",
    "run_backtest_sync",
    "score_query",
]
