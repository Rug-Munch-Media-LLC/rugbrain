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
    through as-is.

    Two rules, both of them the point of this adapter:

    * the case's OWN collection is searched (the retriever's global default is
      a different corpus entirely);
    * ``tier="unavailable"`` RAISES. The old code returned ``result.contexts
      or []``, so a dead embedder graded as "no hits" and the runner recorded
      no error. Asserting on the artifact (a real search that produced
      documents) is what makes the score mean something.
    """
    from rugbrain.search.dual_tier import TIER_UNAVAILABLE, DualTierRetriever

    result = await DualTierRetriever().retrieve(query, top_k=top_k, collection=collection)
    if result.tier == TIER_UNAVAILABLE:
        raise RuntimeError(f"retrieval unavailable: {result.reason}")
    return list(result.contexts or [])


def _default_retriever() -> Callable[..., Any]:
    return _qdrant_retriever


def _embedder_alive() -> tuple[bool, str]:
    """Can a query actually be embedded right now?

    This exists because a retrieval path that DEGRADES SILENTLY reports zero
    docs with no error, so an infrastructure outage is indistinguishable from
    a corpus that genuinely cannot answer — and the gate would then fail
    every capability class on every blip. A gate that cries wolf during
    incidents gets ignored, which is worse than no gate at all. So probe first
    and report UNAVAILABLE separately from a regression.

    The probe goes through :func:`embed_query` — the SAME seam
    :class:`~rugbrain.search.dual_tier.DualTierRetriever` embeds with. It
    used to POST to ``QWEN_EMBED_URL`` directly, which probed the retriever's
    own (now retired) local lane: a system could be perfectly healthy on
    DeepInfra while the preflight reported it dead. One seam, one probe.
    """
    from rugbrain.search.dim_guard import configured_dim
    from rugbrain.search.dual_tier import embed_query

    want = configured_dim()
    try:
        vec = asyncio.run(embed_query("liveness probe"))
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    got = len(vec)
    if got != want:
        return False, f"query_dim_mismatch embedder={got} configured={want}"
    return True, f"query embedding dim = {got} (matches RAG_EMBED_DIM={want})"


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
