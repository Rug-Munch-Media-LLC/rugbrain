"""eval_editorial_rerank.py — rerank A/B on rugbrain's OWN editorial/news corpus.

Ten realistic editorial questions, each with one verified gold document (gold =
the real article the question was written from, so the label is objective — no
model judged its own answer).

    python3 scripts/eval_editorial_rerank.py

Prints recall@1 / recall@5 / MRR for plain ANN vs retrieve-then-rerank, plus the
score spread of both lanes. Exit 0 either way — a measurement, not a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_rerank_ab import reranking_retriever  # noqa: E402

from rugbrain.eval.golden_set import load_golden  # noqa: E402
from rugbrain.eval.harness import direct_qdrant_retriever, run_backtest  # noqa: E402

GOLDEN = ROOT / "eval" / "golden" / "editorial_news_golden.jsonl"


async def _metrics(label: str, retriever, limit: int) -> None:
    rep = await run_backtest(
        corpus=str(GOLDEN), retriever=retriever, top_k=20, k_values=(1, 5), limit=limit
    )
    print(
        f"{label:<7} n={rep.total:<3} errors={rep.errors:<3} "
        f"recall@1={rep.mean_recall.get(1, 0.0):.3f} "
        f"recall@5={rep.mean_recall.get(5, 0.0):.3f} mrr={rep.mean_mrr:.3f}"
    )


async def _spread(limit: int) -> None:
    plain, ranked = direct_qdrant_retriever(), reranking_retriever(20)
    dense_top1, dense_gap, rr_top1, rr_gap = [], [], [], []
    for ex in load_golden(GOLDEN)[:limit]:
        hits = await plain(ex.query, ex.collection, 10)
        sims = [float(h.get("similarity") or 0.0) for h in hits]
        if sims:
            dense_top1.append(sims[0])
            dense_gap.append(sims[0] - sims[-1])
        rk = await ranked(ex.query, ex.collection, 5)
        rs = [float(h.get("rerank_score") or 0.0) for h in rk]
        if len(rs) >= 2:
            rr_top1.append(rs[0])
            rr_gap.append(rs[0] - rs[-1])

    def fmt(v: list[float]) -> str:
        return f"{min(v):.3f}-{max(v):.3f} (mean {sum(v) / len(v):.3f})" if v else "n/a"

    print(
        f"dense  top1 sim {fmt(dense_top1)}  top1-last gap mean {sum(dense_gap) / max(len(dense_gap), 1):.3f}"
    )
    print(
        f"rerank top1 scr {fmt(rr_top1)}  top1-last gap mean {sum(rr_gap) / max(len(rr_gap), 1):.3f}"
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    await _metrics("plain", direct_qdrant_retriever(), args.limit)
    await _metrics("rerank", reranking_retriever(20), args.limit)
    await _spread(args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
