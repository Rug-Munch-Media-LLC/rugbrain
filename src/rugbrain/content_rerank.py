"""Rerank content candidates before they become a post.

`content_candidates.recent_stories` fetches by vector similarity with a
deliberately low floor (``min_similarity=0.15``) so a quiet cycle still returns
something; the cross-encoder is the triage that makes that floor safe.

Two lanes, in this order:
1. **Local corpus lane** — Qwen3-Reranker-8B Q8_0 on ``llama-rerank.service``
   (``:8003``), token-authenticated. It answers when the hosted chain is
   rate-limited or overdue (DeepInfra 402, 2026-10-22). 8B head only — never
   the retired 0.6B speed tier.
2. **Hosted chain** — ``search.rerank.rerank_chunks`` (siliconflow →
   openrouter → deepinfra, all Qwen3-Reranker-8B).

**Fail-open, with a reason.** A reranker outage must not silence the channel:
every returned story carries ``rerank_method`` and ``rerank_reason``.
``search.rerank_pipeline.tag_unreranked`` returns stories WITHOUT a
``rerank_score`` on the degrade path, so a caller that only reads scores drops
every story and posts nothing — the bug this module guards against.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: One standing editorial query, so a score means the same thing run to run.
EDITORIAL_INTENT = "security exploit hack rug pull scam alert important development"

#: Cross-encode only the strongest candidates; beyond this the gain is noise.
CANDIDATE_POOL = 30

DEFAULT_KEEP = 8  #: stories a post is built from

#: Below this score a candidate is off-topic for the channel and dropped, so
#: padding never fills the quota. Live :8003 measurement: on-intent exploit
#: story ~0.22, off-topic market recap ~2e-05.
MIN_RELEVANCE = 0.15

#: Local 8B lane (``llama-rerank.service``); suffix join owned by rerank_endpoint.
LOCAL_RERANK_URL = os.getenv("CONTENT_RERANK_URL", "http://127.0.0.1:8003")
LOCAL_RERANK_MODEL = "qwen3-reranker-8b"
LOCAL_RERANK_TIMEOUT = float(os.getenv("CONTENT_RERANK_TIMEOUT", "45"))

MAX_DOC_CHARS = 2000  #: local lane runs 8192 ctx across 4 slots


def _original_score(story: dict[str, Any]) -> float:
    """Retrieval score, used to bound the pool and as the fallback order."""
    for key in ("score", "similarity", "rrf_score", "rerank_score"):
        value = story.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _doc_text(story: dict[str, Any]) -> str:
    """Text the cross-encoder sees: title first, then content."""
    title = str(story.get("title") or "").strip()
    body = str(story.get("content") or "").strip()
    return f"{title}\n{body}".strip()[:MAX_DOC_CHARS]


def _auth_headers() -> dict[str, str]:
    """Bearer header for the token-protected local lane, or {} when unkeyed.

    Read per call so the key rotates with a restart. One key file
    (/srv/models/embed-lane.env) backs both lanes; without it every :8003 call
    is a hard 401 — why the local rung looked "torched", not unreachable.
    """
    key = (os.getenv("QWEN_EMBED_API_KEY") or os.getenv("RERANKER_API_KEY") or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _local_scores(
    intent: str, candidates: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], float]], str] | None:
    """Score `candidates` on the local 8B lane. None when it cannot answer."""
    from rugbrain.rerank_endpoint import rerank_endpoint

    documents = [_doc_text(s) for s in candidates]
    if not any(documents):
        return None
    payload = {
        "model": LOCAL_RERANK_MODEL,
        "query": intent,
        "documents": documents,
        "top_n": len(documents),
    }
    try:
        async with httpx.AsyncClient(timeout=LOCAL_RERANK_TIMEOUT) as client:
            resp = await client.post(
                rerank_endpoint(LOCAL_RERANK_URL), headers=_auth_headers(), json=payload
            )
        if resp.status_code != 200:
            return None
        results = resp.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("content_rerank_local_unavailable: %s", str(exc)[:120])
        return None
    pairs: list[tuple[dict[str, Any], float]] = []
    for item in results:  # index → story, because the lane returns unordered
        idx = item.get("index")
        score = item.get("relevance_score")
        if isinstance(idx, int) and 0 <= idx < len(candidates) and isinstance(score, (int, float)):
            pairs.append((candidates[idx], float(score)))
    if len(pairs) != len(candidates):
        return None  # a partial answer is not an ordering
    return pairs, f"local-8003:{LOCAL_RERANK_MODEL}"


async def _hosted_scores(
    intent: str, candidates: list[dict[str, Any]], pool: int
) -> tuple[list[tuple[dict[str, Any], float]], str] | None:
    """Second rung: the shared hosted rerank chain. None when it no-ops."""
    try:
        from rugbrain.search.rerank import rerank_chunks  # local import: cycle

        reranked = await rerank_chunks(intent, candidates, top_n=pool, keep=pool, tier="fast")
    except Exception as exc:  # transport/chain failure degrades, never raises
        logger.warning("content_rerank_hosted_unavailable: %s", str(exc)[:120])
        return None
    pairs = [
        (story, float(story["rerank_score"]))
        for story in reranked
        if isinstance(story.get("rerank_score"), (int, float))
    ]
    if not pairs:
        return None
    method = str(reranked[0].get("rerank_method") or "hosted")
    return pairs, f"hosted:{method}"


def _fail_open(candidates: list[dict[str, Any]], keep: int, reason: str) -> list[dict[str, Any]]:
    """Retrieval order, tagged. Never empty for a non-empty pool."""
    logger.warning("content_rerank_degraded reason=%s pool=%d", reason, len(candidates))
    out: list[dict[str, Any]] = []
    for story in sorted(candidates, key=_original_score, reverse=True)[:keep]:
        entry = dict(story)
        entry["rerank_method"] = "none"
        entry["rerank_reason"] = reason
        out.append(entry)
    return out


async def rerank_stories(
    stories: list[dict[str, Any]],
    *,
    intent: str = EDITORIAL_INTENT,
    keep: int = DEFAULT_KEEP,
    pool: int = CANDIDATE_POOL,
    min_relevance: float = MIN_RELEVANCE,
) -> list[dict[str, Any]]:
    """Order `stories` by editorial relevance, best first, capped at `keep`.

    Local 8B first, hosted chain second, retrieval order third. Every story
    names the lane that ordered it (`rerank_method`) and, when no cross-encoder
    ran, why (`rerank_reason`). If the reranker answered and every candidate is
    below `min_relevance`, an empty list is the honest answer.
    """
    if not stories:
        return []
    candidates = sorted(stories, key=_original_score, reverse=True)[:pool]

    scored = await _local_scores(intent, candidates)
    if scored is None:
        scored = await _hosted_scores(intent, candidates, pool)
    if scored is None:
        return _fail_open(candidates, keep, "rerank_unavailable:local+hosted")

    pairs, method = scored
    ordered = sorted(pairs, key=lambda pair: pair[1], reverse=True)
    kept = [(s, sc) for s, sc in ordered if sc >= min_relevance]
    if not kept:
        logger.info(
            "content_rerank_all_below_floor count=%d floor=%.2f method=%s",
            len(pairs),
            min_relevance,
            method,
        )
        return []
    out: list[dict[str, Any]] = []
    for story, score in kept[:keep]:
        entry = dict(story)
        entry["rerank_score"] = round(score, 6)
        entry["rerank_method"] = method
        out.append(entry)
    logger.info(
        "content_rerank_ok method=%s pool=%d kept=%d top=%.4f",
        method,
        len(candidates),
        len(out),
        kept[0][1],
    )
    return out
