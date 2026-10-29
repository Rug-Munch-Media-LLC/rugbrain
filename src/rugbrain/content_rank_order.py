"""Final ordering of an already-selected pool, on the rerank lane.

Selection and ORDER are different jobs. The value score (and the publication
gate) decide WHICH items are worth posting; this module decides the sequence
they appear in, and it does that with the rerank lane rather than a new model —
one cross-encoder, already deployed and already keyed.

Lane: Qwen3-Reranker-0.6B Q8_0 on ``:8030`` (``llama-rerank-06b.service``).

**No probability floor here, on purpose.** Measured 2026-10-28 on the live lane
over the 12-item fixture set: the 0.6B head returns LOGIT-LIKE scores — the
best item scored 0.0051 and the median 0.0001 — whereas the 8B lane that
:mod:`rugbrain.content_rerank` uses returns probabilities (an on-intent story
scored 0.22 there). Applying the 8B's ``MIN_RELEVANCE = 0.15`` to this lane
would silently drop the entire pool. So this module uses the lane to ORDER and
never to admit: nothing is discarded for a low score, and the failure mode of a
mis-set floor cannot empty the channel.

``rerank_share`` is a softmax over the pool supplied for display and for
tie-breaks; the ORDER is always the lane's raw ranking.

Fail-open: if the lane is unreachable the pool comes back ordered by value (then
importance), every story tagged ``rerank_method="none"`` with the reason — the
same contract :mod:`rugbrain.content_rerank` uses.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import httpx

from rugbrain.rerank_endpoint import rerank_endpoint

#: Local 0.6B rerank lane (``llama-rerank-06b.service``).
RERANK_URL = os.getenv("CONTENT_RERANK_LANE_URL", "http://127.0.0.1:8030")
RERANK_MODEL = os.getenv("CONTENT_RERANK_LANE_MODEL", "qwen3-reranker-0.6b")
TIMEOUT = float(os.getenv("CONTENT_RERANK_LANE_TIMEOUT", "45"))

#: Standing query so a score means the same thing run to run (same string the
#: 8B triage uses — one editorial intent for the whole pipeline).
INTENT = "security exploit hack rug pull scam alert important development"

MAX_DOC_CHARS = 2000
#: One key file backs the model lanes; the env wins when compose sets it.
KEY_FILES = ("/srv/models/rerank-lane.env", "/srv/models/embed-lane.env")


def _key() -> str:
    """Bearer key for the lane: env first, then the lane env file."""
    for name in ("CONTENT_RERANK_LANE_API_KEY", "QWEN_EMBED_API_KEY", "RERANKER_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    for path in KEY_FILES:
        try:
            for line in Path(path).read_text().splitlines():
                if line.endswith("_KEY=not-needed"):
                    continue
                if "_KEY=" in line:
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def _doc(story: dict[str, Any]) -> str:
    return f"{story.get('title') or ''}\n{story.get('content') or ''}".strip()[:MAX_DOC_CHARS]


def _fallback(stories: list[dict[str, Any]], reason: str) -> list[dict[str, Any]]:
    """Value order, tagged. Never empty for a non-empty pool."""
    ordered = sorted(
        stories,
        key=lambda s: (float(s.get("value") or 0.0), float(s.get("importance") or 0.0)),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for story in ordered:
        entry = dict(story)
        entry["rerank_method"] = "none"
        entry["rerank_reason"] = reason
        out.append(entry)
    return out


async def order_by_rerank(
    stories: list[dict[str, Any]], *, intent: str = INTENT, url: str | None = None
) -> list[dict[str, Any]]:
    """Order ``stories`` best-first on the 0.6B rerank lane.

    Every returned story carries ``rerank_score`` (raw lane score),
    ``rerank_share`` (softmax within this pool) and ``rerank_method``. Nothing
    is dropped: the caller already decided the pool.
    """
    if not stories:
        return []
    documents = [_doc(story) for story in stories]
    if not any(documents):
        return _fallback(stories, "empty_documents")
    payload = {
        "model": RERANK_MODEL,
        "query": intent,
        "documents": documents,
        "top_n": len(documents),
    }
    key = _key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                rerank_endpoint(url or RERANK_URL), headers=headers, json=payload
            )
        if response.status_code != 200:
            return _fallback(stories, f"lane_http_{response.status_code}")
        results = response.json().get("results") or []
    except (httpx.HTTPError, ValueError) as exc:
        return _fallback(stories, f"lane_unavailable:{str(exc)[:80]}")

    pairs: list[tuple[dict[str, Any], float]] = []
    for item in results:  # index → story: the lane returns them unordered
        index = item.get("index")
        score = item.get("relevance_score")
        if isinstance(index, int) and 0 <= index < len(stories) and isinstance(score, (int, float)):
            pairs.append((stories[index], float(score)))
    if len(pairs) != len(stories):
        return _fallback(stories, "lane_partial_answer")
    return _tag(pairs, f"local-8030:{RERANK_MODEL}")


def _tag(pairs: list[tuple[dict[str, Any], float]], method: str) -> list[dict[str, Any]]:
    """Attach raw score and softmax share, ordered best-first."""
    ordered = sorted(pairs, key=lambda pair: pair[1], reverse=True)
    top = ordered[0][1]
    weights = [math.exp(score - top) for _story, score in ordered]
    total = sum(weights)
    out: list[dict[str, Any]] = []
    for (story, score), weight in zip(ordered, weights, strict=True):
        entry = dict(story)
        entry["rerank_score"] = score
        entry["rerank_share"] = round(weight / total, 6)
        entry["rerank_method"] = method
        out.append(entry)
    return out


__all__ = ["INTENT", "RERANK_MODEL", "RERANK_URL", "order_by_rerank"]
