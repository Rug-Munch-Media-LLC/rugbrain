"""One pass: tri-dedupe → value score → route → final ordering.

The stages live in their own modules; this is the glue that runs them in the
only order that is honest:

    dedupe first   a duplicate must not consume a channel's daily budget
    score next     novelty is measured against the survivors, not the raw pool
    route after    the gate and the tier ladder both need the final score
    order last     the rerank lane sequences picks that already earned a slot

Two live lanes are touched, both injected so a test never needs a network:

    embed   the corpus lane (4096d) for the semantic duplicate layer
    rerank  the 0.6B lane for the final ordering (:mod:`content_rank_order`)

Both fail open and say so in ``lane`` — a dead lane degrades to fewer layers or
a value-ordered pool, it never empties the run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from rugbrain import content_rank_order
from rugbrain.content_dedupe import collapse_with_report
from rugbrain.content_history import SeenLedger
from rugbrain.content_routing import route_item
from rugbrain.content_value import score_candidate

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
OrderFn = Callable[..., Awaitable[list[dict[str, Any]]]]


async def _default_embed(texts: list[str]) -> list[list[float]]:
    """Corpus-lane document embeddings (4096d), imported late for the seamy test seam."""
    from rugbrain.embeddings import get_embeddings

    return await get_embeddings(texts)


async def _vectors(
    stories: list[dict[str, Any]], embed: EmbedFn
) -> tuple[dict[str, list[float]], str]:
    """Embeddings keyed by story id; empty dict plus a reason on failure."""
    texts = [f"{s.get('title') or ''}\n{s.get('content') or ''}".strip() for s in stories]
    if not any(texts):
        return {}, "empty_documents"
    try:
        embedded = await embed(texts)
    except Exception as exc:  # lane down: the first two layers still run
        return {}, f"embed_unavailable:{str(exc)[:100]}"
    if len(embedded) != len(stories):
        return {}, "embed_partial_answer"
    return {
        str(story.get("id") or index): vector
        for index, (story, vector) in enumerate(zip(stories, embedded, strict=True))
    }, ""


async def cream_of_the_crop(
    stories: list[dict[str, Any]],
    *,
    day: str,
    seen: SeenLedger | None = None,
    embed: EmbedFn | None = None,
    order: OrderFn | None = None,
    now: datetime | None = None,
    reorder: bool = True,
) -> dict[str, Any]:
    """Dedupe, score and route a pool for one day.

    Returns ``{"picks", "suppressed", "lane", "ordered"}`` where each pick is
    the routing decision plus its story, value and breakdown. ``ordered`` is the
    rerank lane's sequence over the picks that earned a broadcast slot.
    """
    ledger = seen if seen is not None else SeenLedger()
    vectors, embed_reason = await _vectors(stories, embed or _default_embed)
    kept, suppressed = collapse_with_report(stories, vectors=vectors)

    moment = now or datetime.now(UTC)
    picks: list[dict[str, Any]] = []
    for story in kept:
        vector = vectors.get(str(story.get("id") or ""))
        repeat = ledger.repeat_of(story, vector=vector)
        evidence = repeat or {
            "exact": False,
            "jaccard": 0.0,
            "cosine": None,
            "similarity": float(story.get("dedupe_similarity") or 0.0),
        }
        scored = score_candidate(story, now=moment, evidence=evidence)
        decision = route_item(
            story,
            day=day,
            seen=ledger,
            value=scored["value"],
            duplicate=repeat is not None,
            now=moment,
        )
        picks.append(
            {
                "story": story,
                "value": scored["value"],
                "value_breakdown": scored["value_breakdown"],
                "repeat": None
                if repeat is None
                else {
                    "layer": repeat["layer"],
                    "id": repeat["entry"].id,
                    "similarity": round(float(repeat["similarity"]), 4),
                },
                **decision,
            }
        )

    picks.sort(key=lambda pick: pick["value"], reverse=True)
    broadcast = [pick for pick in picks if pick["route"] in ("channel", "both")]
    ordered: list[dict[str, Any]] = []
    order_reason = "disabled"
    if reorder and broadcast:
        lane = await (order or content_rank_order.order_by_rerank)(
            [pick["story"] for pick in broadcast]
        )
        by_id = {str(pick["story"].get("id") or ""): pick for pick in broadcast}
        for entry in lane:
            pick = by_id.get(str(entry.get("id") or ""))
            if pick is not None:
                pick["rerank_score"] = entry.get("rerank_score")
                pick["rerank_share"] = entry.get("rerank_share")
                pick["rerank_method"] = entry.get("rerank_method")
                ordered.append(pick)
        if ordered:
            order_reason = str(ordered[0].get("rerank_method") or "unknown")
    return {
        "picks": picks,
        "suppressed": suppressed,
        "ordered": ordered,
        "lane": {"embed": embed_reason or "ok", "order": order_reason},
    }


__all__ = ["EmbedFn", "OrderFn", "cream_of_the_crop"]
