#!/usr/bin/env python3
"""eval_ingest_sources.py — retrieval-quality eval harness for ingest sources.

Queries the RugBrain API per collection (rerank on), computes recall@k (k=5,10),
MRR@10, nDCG@10 vs expected ids/addresses, prints a compact table, writes
eval/reports/ingest_baseline_<date>.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

# Add src to path for rugbrain imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rugbrain.eval.backtest_report import BacktestReport, QueryScore
from rugbrain.eval.golden_set import GoldenQuery, load_golden
from rugbrain.eval.ndcg import mrr, ndcg_at_k, recall_at_k
from rugbrain.eval.query_scoring import score_query


def _load_api_key() -> str:
    """Load the API key from the rugbrain secrets file."""
    # Try the secrets file first (likely the most current)
    secrets_path = Path("/srv/secrets/rugbrain.env")
    if secrets_path.exists():
        content = secrets_path.read_text()
        match = re.search(r"RMI_RAG_API_KEY=([^\n]+)", content)
        if match:
            key = match.group(1).strip()
            if "#" in key:
                key = key.split("#")[0].strip()
            return key
    # Fallback to .env.rag in the repo
    env_path = Path("/srv/work/repos/rugbrain/.env.rag")
    if env_path.exists():
        content = env_path.read_text()
        match = re.search(r"RMI_RAG_API_KEY=([^\n]+)", content)
        if match:
            key_line = match.group(1).strip()
            if "#" in key_line:
                key_line = key_line.split("#")[0].strip()
            # Take first key if comma-separated (legacy format)
            return key_line.split(",")[0].strip()
    # Last resort: environment variable
    key = os.getenv("RMI_RAG_API_KEY")
    if key:
        return key.split(",")[0].strip()
    raise RuntimeError("Could not find RMI_RAG_API_KEY in secrets or env")


RUGBRAIN_API = os.getenv("RUGBRAIN_API", "http://127.0.0.1:8012").rstrip("/")
API_KEY = _load_api_key()
GOLDEN_PATH = (
    Path(__file__).resolve().parent.parent
    / "eval"
    / "golden"
    / "ingest_sources_golden.jsonl"
)
REPORT_DIR = (
    Path(__file__).resolve().parent.parent
    / "eval"
    / "reports"
)


async def rugbrain_retriever(
    query: str,
    collection: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Call RugBrain API /api/v1/search/multi with rerank=true."""
    url = f"{RUGBRAIN_API}/api/v1/search/multi"
    payload = {
        "query": query,
        "collections": [collection],
        "limit": limit,
        "rerank": True,
    }
    headers = {"X-API-Key": API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"RugBrain API error {resp.status}: {text}")
            data = await resp.json()
    results = data.get("results", [])
    # Ensure each result has an 'id' field that matches the golden set's relevant_doc_ids
    # The API returns sanitized public results; the doc_id might be in 'doc_id' or 'id'.
    for r in results:
        if "id" not in r and "doc_id" in r:
            r["id"] = r["doc_id"]
    return results


def compute_metrics(queries: list[GoldenQuery], per_query_scores: list[QueryScore], k_values: list[int]) -> dict:
    """Aggregate per-collection metrics."""
    # Group by collection
    by_col = defaultdict(list)
    for q, s in zip(queries, per_query_scores):
        if not s.error:
            by_col[q.collection].append(s)

    summary = {}
    for col, scores in by_col.items():
        if not scores:
            continue
        n = len(scores)
        col_metrics = {"total_queries": n, "errors": 0}
        for k in k_values:
            col_metrics[f"recall@{k}"] = sum(s.recall.get(k, 0.0) for s in scores) / n
            col_metrics[f"ndcg@{k}"] = sum(s.ndcg.get(k, 0.0) for s in scores) / n
        col_metrics["mrr@10"] = sum(s.mrr_value for s in scores) / n
        col_metrics["p50_latency_ms"] = sorted(s.latency_ms for s in scores)[n // 2]
        summary[col] = col_metrics
    return summary


async def main() -> int:
    # Load golden set
    if not GOLDEN_PATH.exists():
        print(f"Golden set not found at {GOLDEN_PATH}", file=sys.stderr)
        return 1

    queries = load_golden(GOLDEN_PATH)
    print(f"Loaded {len(queries)} golden queries from {GOLDEN_PATH}")

    k_values = [5, 10]
    top_k = 10

    # Run each query through the API
    per_query_scores: list[QueryScore] = []
    for ex in queries:
        score = await score_query(
            retriever=rugbrain_retriever,
            example=ex,
            top_k=top_k,
            k_values=k_values,
        )
        per_query_scores.append(score)
        if score.error:
            print(f"  ERROR: {ex.collection} - {ex.query[:50]}: {score.error}")
        else:
            print(f"  OK: {ex.collection} - recall@5={score.recall.get(5,0):.3f} recall@10={score.recall.get(10,0):.3f} mrr={score.mrr_value:.3f}")

    # Compute per-collection metrics
    metrics = compute_metrics(queries, per_query_scores, k_values)

    # Print compact table
    print("\n=== Ingest Sources Baseline ===")
    print(f"{'Collection':<25} {'Queries':>7} {'Recall@5':>9} {'Recall@10':>10} {'MRR@10':>8} {'nDCG@5':>8} {'nDCG@10':>9} {'p50_ms':>7}")
    for col in sorted(metrics.keys()):
        m = metrics[col]
        print(
            f"{col:<25} {m['total_queries']:>7} "
            f"{m.get('recall@5', 0):>9.3f} {m.get('recall@10', 0):>10.3f} "
            f"{m.get('mrr@10', 0):>8.3f} "
            f"{m.get('ndcg@5', 0):>8.3f} {m.get('ndcg@10', 0):>9.3f} "
            f"{m.get('p50_latency_ms', 0):>7.0f}"
        )

    # Overall averages
    all_scores = [s for s in per_query_scores if not s.error]
    if all_scores:
        n = len(all_scores)
        overall = {
            "recall@5": sum(s.recall.get(5, 0.0) for s in all_scores) / n,
            "recall@10": sum(s.recall.get(10, 0.0) for s in all_scores) / n,
            "mrr@10": sum(s.mrr_value for s in all_scores) / n,
            "ndcg@5": sum(s.ndcg.get(5, 0.0) for s in all_scores) / n,
            "ndcg@10": sum(s.ndcg.get(10, 0.0) for s in all_scores) / n,
            "p50_latency_ms": sorted(s.latency_ms for s in all_scores)[n // 2],
        }
        print(
            f"{'OVERALL':<25} {n:>7} "
            f"{overall['recall@5']:>9.3f} {overall['recall@10']:>10.3f} "
            f"{overall['mrr@10']:>8.3f} "
            f"{overall['ndcg@5']:>8.3f} {overall['ndcg@10']:>9.3f} "
            f"{overall['p50_latency_ms']:>7.0f}"
        )

    # Write JSON report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(UTC).strftime("%Y%m%d")
    report_path = REPORT_DIR / f"ingest_baseline_{date_str}.json"

    report = {
        "date": datetime.now(UTC).isoformat(),
        "golden_set": str(GOLDEN_PATH),
        "total_queries": len(queries),
        "successful_queries": len(all_scores),
        "errors": len(queries) - len(all_scores),
        "per_collection": metrics,
        "overall": overall if all_scores else {},
    }

    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
