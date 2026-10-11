"""eval_rerank_ab.py — does the reranker actually help? A/B on the golden set.

The smoke eval measures the raw dense retrieval path; the production search
also runs Qwen3-Reranker-8B over the candidates (local :8003, DeepInfra
hosted fallback). Without an A/B we cannot tell whether the rerank lane earns
its latency.

Runs the golden set twice — plain ANN retrieval vs. retrieve-then-rerank
(top-20 candidates scored, top-k kept) — and prints recall@k / nDCG@k / MRR
for both, plus the latency delta.

Usage:
    python3 scripts/eval_rerank_ab.py [--limit 20] [--top-k 10] [--candidates 20]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rugbrain.eval.harness import direct_qdrant_retriever, run_backtest


def reranking_retriever(candidates: int = 20):
    """Wrap the plain retriever: fetch ``candidates``, rerank, keep the order."""
    plain = direct_qdrant_retriever()

    async def retrieve(query: str, collection: str | None, limit: int) -> list[dict]:
        from rugbrain.reranker import rerank as _rerank

        pool = await plain(query, collection, max(candidates, limit))
        if not pool:
            return []
        docs = [str(item.get("content") or "")[:2000] for item in pool]
        scored = await _rerank(query, docs, top_k=limit)
        if not scored:
            return pool[:limit]  # fail-open: keep the pre-rerank order
        out: list[dict] = []
        for hit in scored:
            idx = hit.get("index")
            if isinstance(idx, int) and 0 <= idx < len(pool):
                item = dict(pool[idx])
                item["rerank_score"] = hit.get("relevance_score", 0.0)
                out.append(item)
        return out or pool[:limit]

    return retrieve


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="golden queries to run")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--candidates", type=int, default=20)
    args = ap.parse_args()

    k_values = (5, args.top_k)
    plain = await run_backtest(
        retriever=direct_qdrant_retriever(), top_k=args.top_k, k_values=k_values, limit=args.limit
    )
    ranked = await run_backtest(
        retriever=reranking_retriever(args.candidates),
        top_k=args.top_k,
        k_values=k_values,
        limit=args.limit,
    )

    print(f"queries: {plain.total} plain / {ranked.total} rerank (errors: {plain.errors}/{ranked.errors})")
    print(f"{'metric':<14} {'plain':>10} {'rerank':>10} {'delta':>10}")
    rows = [(f"recall@{k}", plain.mean_recall.get(k, 0.0), ranked.mean_recall.get(k, 0.0)) for k in k_values]
    rows += [(f"ndcg@{k}", plain.mean_ndcg.get(k, 0.0), ranked.mean_ndcg.get(k, 0.0)) for k in k_values]
    rows.append(("mrr", plain.mean_mrr, ranked.mean_mrr))
    for name, a, b in rows:
        print(f"{name:<14} {a:>10.4f} {b:>10.4f} {b - a:>+10.4f}")
    p50 = lambda r: (sorted(q.latency_ms for q in r.per_query)[len(r.per_query) // 2] if r.per_query else 0.0)
    print(f"{'p50_ms':<14} {p50(plain):>10.0f} {p50(ranked):>10.0f} {p50(ranked) - p50(plain):>+10.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
