"""Content engine — turns reranked RAG stories into channel-ready posts.

Automated content-distribution core (2026-08-24 directive):

    RAG stories (embedded, deduped, importance-reranked)
        → per-channel selection rules
        → GOOD WRITING pass (analysis + recap + link; strict no-slop prompt
          over the local llama.cpp chat model, template fallback that is
          still clean prose)
        → structured drafts consumed by the bot publisher, which posts to
          Telegram and mirrors @cryptorugmuncher posts to feed.rugmunch.io

Channel routing rules:
    main    (@cryptorugmuncher)  top 1–3 stories of the cycle, full analysis,
                                 mirrored to the feed blog
    alerts  (@rmialerts)         critical/high severity only
    news    (@rmicryptonews)     solid news-value items, tighter format
    scans   (@rmiscans)          real scanner output with reasoning + ref links

Quality bar (enforced in prompts AND fallback templates):
    - No hype words. Every post: what happened → why it matters → source.
    - Scanner posts include actual verdict fields + deep-dive URL.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("rugbrain.content")

CHAT_URL = os.getenv("RAG_CHAT_URL", "http://127.0.0.1:8009/v1/chat/completions")
CHAT_MODEL = os.getenv("RAG_CHAT_MODEL", "qwen3-8b-instruct")
CHAT_TIMEOUT = float(os.getenv("RAG_CHAT_TIMEOUT", "45"))

SCANNER_BASE = os.getenv("RMI_SCANNER_BASE_URL", "https://rugmunch.io/scan")

MAX_MAIN_PER_CYCLE = int(os.getenv("RAG_CONTENT_MAX_MAIN", "3"))

WRITER_SYSTEM_PROMPT = (
    "You are the editor of a crypto-security intelligence desk. "
    "Rewrite the provided story notes into a Telegram post. HARD RULES: "
    "(1) Open with one factual sentence stating what happened. "
    "(2) Add 1-2 sentences of analysis: why it matters, who is affected. "
    "(3) Close with 'Source: ' + the given link. "
    "(4) No emojis except at most one severity marker. No hype words "
    "(insane, crazy, moon, rocket). No hashtags. No speculation presented "
    "as fact. Keep under 120 words. Output ONLY the post text."
)


async def _write_post(notes: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as c:
            r = await c.post(
                CHAT_URL,
                json={
                    "model": CHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": WRITER_SYSTEM_PROMPT},
                        {"role": "user", "content": notes[:6000]},
                    ],
                    "temperature": 0.4,
                    "max_tokens": 320,
                },
            )
            if r.status_code != 200:
                return None
            content = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            text = (content or "").strip()
            if "</think>" in text:
                text = text.split("</think>", 1)[1].strip()
            return text or None
    except Exception as e:
        logger.debug("writer_unavailable err=%s", str(e)[:100])
        return None


def _template_post(story: dict[str, Any]) -> str:
    title = (story.get("title") or story.get("content", "")[:90]).strip()
    sev = story.get("severity", "medium").upper()
    src = story.get("source", "telegram")
    url = story.get("url") or ""
    lines = [f"[{sev}] {title}"]
    body = (story.get("content") or "").strip()
    if len(body) > 260:
        body = body[:257].rsplit(" ", 1)[0] + "…"
    if body and body.lower() != title.lower():
        lines.append("")
        lines.append(body)
    lines.append("")
    lines.append(f"Source: {src}" + (f" · {url}" if url else ""))
    return "\n".join(lines)


async def _compose(story: dict[str, Any]) -> str:
    notes = (
        f"Story notes:\n{story.get('content', '')[:3000]}\n\n"
        f"Severity: {story.get('severity', 'medium')}\n"
        f"Source link: {story.get('url') or '(none provided)'}"
    )
    return await _write_post(notes) or _template_post(story)


async def _recent_stories(limit: int = 60) -> list[dict[str, Any]]:
    from rugbrain.embeddings import get_query_embedding
    from rugbrain.qdrant_store import get_qdrant_store

    store = await get_qdrant_store()
    embedding = await get_query_embedding(
        "security exploit hack rug pull scam alert important development"
    )
    seen: dict[str, dict[str, Any]] = {}
    for collection in ("threat_intel_v2", "news_v2"):
        try:
            results = await store.search(embedding, collection=collection, limit=limit // 2)
        except Exception as e:
            logger.debug("content_probe_failed collection=%s err=%s", collection, str(e)[:80])
            continue
        for r in results:
            md = r.get("metadata", {}) or {}
            key = (r.get("content") or "")[:120]
            if not key or key in seen:
                continue
            seen[key] = {
                "title": md.get("title", ""),
                "content": r.get("content", ""),
                "severity": (md.get("severity") or "medium").lower(),
                "source": r.get("source", ""),
                "url": md.get("url", ""),
                "importance": float(md.get("importance") or 0),
                "kind": md.get("kind", "story"),
                "collection": collection,
            }
    ranked = sorted(
        seen.values(),
        key=lambda s: (
            -s["importance"],
            {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(s["severity"], 2),
        ),
    )
    return ranked[:limit]


async def select_scans(limit: int = 3) -> list[dict[str, Any]]:
    """Scans-of-interest drafts from REAL scanner feedback docs."""
    from rugbrain.qdrant_store import get_qdrant_store

    store = await get_qdrant_store()
    drafts: list[dict[str, Any]] = []
    try:
        points, _ = await store.scroll("scanner_feedback", limit=limit * 4)
    except Exception as e:
        logger.debug("scan_scroll_failed err=%s", str(e)[:80])
        return []

    picked = 0
    for p in points:
        payload = p.get("payload", {}) or {}
        md = payload.get("metadata", {}) or {}
        token = md.get("token_address") or md.get("token") or ""
        chain = md.get("chain", "")
        risk = md.get("risk_score")
        if not token or risk is None:
            continue
        reasons = md.get("reasons") or md.get("red_flags") or []
        reason_lines = "\n".join(
            f"  • {r}" for r in (reasons if isinstance(reasons, list) else [str(reasons)])[:4]
        )
        ref_link = (
            f"{SCANNER_BASE}?token={token}&chain={chain}"
            if chain
            else f"{SCANNER_BASE}?token={token}"
        )
        text = (
            f"[SCAN] {chain.upper() if chain else 'token'} · {token[:10]}…{token[-6:]}\n"
            f"\nRisk score: {risk}/100 ({md.get('risk_level', 'n/a')})"
            + (f"\nWhy:\n{reason_lines}" if reason_lines else "")
            + f"\n\nFull scan output: {ref_link}"
        )
        drafts.append(
            {
                "text": text,
                "story_ref": f"scanner_feedback:{token[:12]}",
                "mirror": False,
                "kind": "scan",
                "ref_link": ref_link,
            }
        )
        picked += 1
        if picked >= limit:
            break
    return drafts


async def generate_channel_drafts(cycle_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    started = time.time()
    stories = await _recent_stories()

    drafts: dict[str, list[dict[str, Any]]] = {"main": [], "alerts": [], "news": [], "scans": []}

    alert_stories = [s for s in stories if s["severity"] in ("critical", "high")]
    for s in alert_stories[:5]:
        drafts["alerts"].append(
            {
                "text": await _compose(s),
                "story_ref": s["source"],
                "mirror": False,
                "kind": s["kind"],
            }
        )

    main_pool = [s for s in stories if s not in alert_stories[:5]]
    for s in main_pool[:MAX_MAIN_PER_CYCLE]:
        text = await _compose(s)
        if s.get("url"):
            text += f"\n\nFull coverage: {s['url']}"
        drafts["main"].append(
            {"text": text, "story_ref": s["source"], "mirror": True, "kind": s["kind"]}
        )

    news_pool = [s for s in main_pool[MAX_MAIN_PER_CYCLE:] if s["severity"] != "low"]
    for s in news_pool[:6]:
        drafts["news"].append(
            {
                "text": await _compose(s),
                "story_ref": s["source"],
                "mirror": False,
                "kind": s["kind"],
            }
        )

    drafts["scans"] = await select_scans()

    logger.info(
        "content_drafts_ready cycle=%s main=%d alerts=%d news=%d scans=%d secs=%.1f",
        cycle_id or "-",
        len(drafts["main"]),
        len(drafts["alerts"]),
        len(drafts["news"]),
        len(drafts["scans"]),
        time.time() - started,
    )
    return drafts
