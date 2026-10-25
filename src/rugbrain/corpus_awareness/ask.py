"""corpus_ask: route -> per-collection search -> merge/dedupe -> cited, budgeted answer."""

from __future__ import annotations

import re
from typing import Any

from rugbrain.corpus_awareness.routes import route_question

_STORY_KEYS = ("story_id", "storyId", "id", "url", "canonical_url", "link")
_TITLE_KEYS = ("title", "headline", "name", "source_title")
_TEXT_KEYS = ("text", "content", "chunk", "body", "summary", "description", "snippet")
DEFAULT_MIN_SCORE = 0.30


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def _story_key(hit: dict[str, Any]) -> str:
    payload = hit.get("payload") or {}
    for key in _STORY_KEYS:
        if payload.get(key):
            return f"{key}:{payload[key]}"
    title = _first(payload, _TITLE_KEYS) or hit.get("text", "")
    slug = re.sub(r"[^a-z0-9]+", "", title.lower())[:90]
    return f"title:{slug}" if slug else f"fallback:{id(hit)}"


def _merge(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for hit in hits:
        key = _story_key(hit)
        current = best.get(key)
        if current is None:
            best[key] = hit
            continue
        current["also_in"] = sorted({*current.get("also_in", []), hit["collection"]})
        if hit["score"] > current["score"]:
            hit["also_in"] = current["also_in"]
            best[key] = hit
    return sorted(best.values(), key=lambda h: h["score"], reverse=True)


async def ask_corpus(
    question: str,
    searcher: Any,
    *,
    budget_tokens: int = 800,
    per_collection: int = 6,
    min_score: float = DEFAULT_MIN_SCORE,
    available: list[str] | None = None,
    collections: list[str] | None = None,
) -> dict[str, Any]:
    """Answer from the corpus only: cite (collection, title, score) or refuse.

    `insufficient_evidence` is returned when nothing clears *min_score* — the
    function never pads an answer from model memory.
    """
    cols = collections or route_question(question, available=available)
    if not cols:
        return {
            "status": "insufficient_evidence",
            "question": question,
            "reason": "no collection routes for this question (routing table empty or unreachable)",
            "collections_searched": [],
            "citations": [],
            "answer": "",
        }
    raw: list[dict[str, Any]] = []
    for name in cols[:5]:
        try:
            found = await searcher.search(name, question, per_collection)
        except Exception as exc:  # noqa: BLE001 - search lane down is a typed refusal
            return {
                "status": "refused",
                "question": question,
                "error": {"code": "search_unreachable", "reason": f"{type(exc).__name__}: {exc}"[:300]},
                "citations": [],
                "answer": "",
            }
        for row in found or []:
            payload = row.get("payload") or row
            raw.append(
                {
                    "collection": name,
                    "score": round(float(row.get("score") or row.get("rerank_score") or 0.0), 3),
                    "title": _first(payload, _TITLE_KEYS) or "(untitled)",
                    "text": _first(payload, _TEXT_KEYS)[:280],
                    "payload": payload,
                }
            )
    kept = _merge([h for h in raw if h["score"] >= min_score])
    if not kept:
        return {
            "status": "insufficient_evidence",
            "question": question,
            "reason": f"no hit cleared min_score={min_score} across {cols[:5]}",
            "collections_searched": cols[:5],
            "citations": [],
            "answer": "",
        }
    char_budget = max(160, budget_tokens * 4)
    lines = [f"{question.strip()[:110]}"]
    citations: list[dict[str, Any]] = []
    for hit in kept:
        support = hit["text"][:150] or "(no body field)"
        line = f"[{len(citations) + 1}] {hit['collection']} | {hit['title']} | {hit['score']} — {support}"
        if sum(len(x) + 1 for x in lines) + len(line) > char_budget:
            break
        lines.append(line)
        citations.append(
            {
                "n": len(citations) + 1,
                "collection": hit["collection"],
                "title": hit["title"],
                "score": hit["score"],
                "also_in": hit.get("also_in", []),
            }
        )
    answer = "\n".join(lines)
    return {
        "status": "ok",
        "question": question,
        "collections_searched": cols[:5],
        "citations": citations,
        "answer": answer,
        "tokens_est": len(answer) // 4,
        "below_budget": len(answer) <= char_budget,
    }
