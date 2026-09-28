"""Runnable entry point: ``python -m rugbrain.missioneval``.

The eval was a library with no entry point, which meant it could never be run on
demand or from a timer — and a gate that cannot be run is not a gate. This runs
the golden set against the REAL production retriever
(:class:`rugbrain.search.dual_tier.DualTierRetriever`), so the score describes
the system that actually serves queries, not a stub.

Exit codes are meaningful:

    0  every measured metric is at or above its baseline floor
    1  a metric regressed below its floor, or the run errored
    2  Qdrant or the embedder is unreachable, so no score is meaningful

The floors in ``mission_baselines.json`` are the numbers MEASURED on 2026-08-28,
not aspirations. Two sit at 0.0 because those capability classes are genuinely
empty today (rug history, technical analysis); raising one is a deliberate,
reviewable edit, never a silent one.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from rugbrain.missioneval.gate import evaluate_mission, format_verdict
from rugbrain.missioneval.run import run_mission_eval

#: Cap on candidates pulled per case, matching the content pipeline.
TOP_K = 10


async def _qdrant_retriever(query: str, collection: str, top_k: int) -> list[dict[str, Any]]:
    """Adapt the production retriever to the eval's ``(query, collection, k)``.

    The eval grades on document text and id, so the result contexts are passed
    through as-is. A failure surfaces as an exception, which the runner records
    as a per-case error rather than a silent miss — a broken retrieval path must
    not be able to look like a poor corpus.
    """
    import aiohttp

    from rugbrain.search.dual_tier import DualTierRetriever

    async with aiohttp.ClientSession() as http:
        retriever = DualTierRetriever(http=http)
        result = await retriever.retrieve(query, top_k=top_k)
    return list(result.contexts or [])


def _default_retriever() -> Callable[..., Any]:
    return _qdrant_retriever


def _embedder_alive() -> tuple[bool, str]:
    """Can a query actually be embedded right now?

    This exists because the dual-tier retriever DEGRADES SILENTLY: when the
    embedder is down it logs "both tiers unavailable" and returns zero docs.
    The runner records no error, so an infrastructure outage is indistinguishable
    from a corpus that genuinely cannot answer — and the gate would then fail
    every capability class on every blip. A gate that cries wolf during
    incidents gets ignored, which is worse than no gate at all. So probe first
    and report UNAVAILABLE separately from a regression.
    """
    try:
        dims = asyncio.run(_probe_embed_dim())
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"query embedding dim = {dims}"


async def _probe_embed_dim() -> int:
    """Probe the SAME lane the dual-tier retriever embeds with.

    `get_query_embedding` resolves `RAG_EMBED_BACKEND=auto` (DeepInfra here),
    but `DualTierRetriever` embeds against `QWEN_EMBED_URL` (the local :8004
    lane). Probing the wrong one reports a healthy system while retrieval
    returns nothing, so the probe must hit the retriever's own URL.
    """
    import os

    import aiohttp

    url = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1").rstrip("/")
    async with aiohttp.ClientSession() as http:
        async with http.post(f"{url}/embeddings", json={"input": "liveness probe"}) as resp:
            resp.raise_for_status()
            return len((await resp.json())["data"][0]["embedding"])


def main() -> int:
    alive, detail = _embedder_alive()
    if not alive:
        print(
            f"mission-eval: UNAVAILABLE - embedder unreachable ({detail}). "
            "This is an infrastructure outage, not a capability regression; "
            "no score is reported.",
            file=sys.stderr,
        )
        return 2
    print(f"mission-eval: preflight ok ({detail})")
    try:
        report = asyncio.run(run_mission_eval(_default_retriever(), None, TOP_K))
    except Exception as exc:
        print(f"mission-eval: UNAVAILABLE ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 2
    if report.errors == report.total and report.total:
        print(
            f"mission-eval: UNAVAILABLE - all {report.total} cases errored; no score is meaningful",
            file=sys.stderr,
        )
        return 2
    verdict = evaluate_mission(report)
    print(format_verdict(verdict))
    print(
        json.dumps(
            {
                "total": report.total,
                "errors": report.errors,
                "gold_rate": report.gold_rate,
                "hit_rate": report.hit_rate,
                "mrr": report.mrr,
                **verdict.to_dict(),
            },
            indent=2,
        )
    )
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
