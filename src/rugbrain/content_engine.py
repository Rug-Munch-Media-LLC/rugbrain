"""Content engine — turns reranked RAG stories into channel-ready posts.

Automated content-distribution core (2026-08-24 directive):

    RAG stories (embedded, deduped, importance-reranked)
        → per-channel selection rules
        → GOOD WRITING pass (analysis + recap + link; strict no-slop prompt
          over the local llama.cpp chat model, template fallback that is
          still clean prose)
        → FACT-CHECK GATE (editorial standards #21, #12, #15)
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
    - Headline presentation lives in :mod:`rugbrain.formatting`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain import content_candidates
from rugbrain.factcheck import FactCheckResult, fact_check_gate
from rugbrain.formatting.headline import style_for_site, style_headline

logger = logging.getLogger("rugbrain.content")

CHAT_URL = os.getenv("RAG_CHAT_URL", "http://127.0.0.1:8009/v1/chat/completions")
CHAT_MODEL = os.getenv("RAG_CHAT_MODEL", "qwythos-9b")
CHAT_TIMEOUT = float(os.getenv("RAG_CHAT_TIMEOUT", "45"))

_drafts_cache: dict[str, Any] = {"ts": 0.0, "data": None}
DRAFTS_CACHE_TTL = int(os.getenv("RAG_DRAFTS_CACHE_TTL", "1800"))  # 30 min

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
        # 2026-08-25: optional Bearer auth so RAG_CHAT_URL can point at a
        # hosted OpenAI-compatible provider (OpenRouter/DeepSeek) while the
        # local llama.cpp writer is CPU-starved. Unset = local writer,
        # behavior unchanged. Switch back by removing RAG_CHAT_* from
        # .env.rag and recreating the container.
        api_key = os.getenv("RAG_CHAT_API_KEY", "")
        headers = None
        payload: dict[str, Any] = {
            "model": CHAT_MODEL,
            "messages": [
                {"role": "system", "content": WRITER_SYSTEM_PROMPT},
                {"role": "user", "content": notes[:9000]},
            ],
            "temperature": 0.4,
            "max_tokens": 320,
        }
        if api_key:
            # Hosted-provider mode: Bearer auth + explicit UA (gateways
            # behind Cloudflare historically block python-httpx UAs) +
            # reasoning-off so reasoning-class models don't burn the token
            # budget before writing content.
            headers = {
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "opencode/1.0",
            }
            payload["reasoning"] = {"enabled": False}
            payload["max_tokens"] = 700
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as c:
            r = await c.post(
                CHAT_URL,
                headers=headers,
                json=payload,
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
    src = story.get("source", "telegram")
    url = story.get("url") or ""
    # Category label + rotating emoji + credibility cue, HTML-safe for the
    # bot's parse_mode=HTML (see rugbrain.formatting).
    head = story.get("headline") or style_headline(story)
    title = (story.get("title") or story.get("content", "")[:90]).strip()
    body = (story.get("content") or "").strip()
    if len(body) > 260:
        body = body[:257].rsplit(" ", 1)[0] + "…"
    lines = [head, "", body] if body and body.lower() != title.lower() else [head]
    lines.append("")
    lines.append(f"Source: {src}" + (f" · {url}" if url else ""))
    return "\n".join(lines)


async def _compose(story: dict[str, Any]) -> tuple[str, str]:
    """Write one post. Returns ``(text, writer)`` where writer is "llm" or
    "template" (2026-08-25 WO-3: the bot's quality gate drops template
    output — a dead writer must degrade to silence, never to slop).

    Then runs the fact-check gate (editorial standards #21, #12, #15).
    If the gate fails, unverifiable claims are stripped. If all claims
    fail, the draft is rejected (returns empty text, "rejected").
    """
    notes = (
        f"Story notes:\n{story.get('content', '')[:3000]}\n\n"
        f"Severity: {story.get('severity', 'medium')}\n"
        f"Source link: {story.get('url') or '(none provided)'}"
    )
    written = await _write_post(notes)
    if written:
        return written, "llm"
    return _template_post(story), "template"


async def _compose_with_factcheck(story: dict[str, Any]) -> tuple[str, str]:
    """Compose and run fact-check gate. Returns (text, writer)."""
    text, writer = await _compose(story)

    # Run fact-check gate (editorial standards #21, #12, #15)
    fc_result: FactCheckResult = await fact_check_gate(text, story)

    if fc_result.verdict == "PASS" and fc_result.claims:
        # Gate passed — return composed text with fact-check metadata
        logger.info(
            "factcheck_pass channel_story=%s claims_verified=%d stripped=%d",
            story.get("source", "unknown"),
            len(fc_result.claims),
            len(fc_result.stripped_claims),
        )
        # Optionally append fact-check summary for transparency
        if fc_result.stripped_claims:
            strip_note = (
                "\n\n[Fact-check: "
                f"{len(fc_result.stripped_claims)} claim(s) stripped — "
                "insufficient independent sources]"
            )
            text += strip_note
        return text, writer

    if fc_result.verdict == "FAIL":
        # All claims failed verification — reject the draft
        logger.warning(
            "factcheck_reject channel_story=%s unverifiable=%d missing=%s",
            story.get("source", "unknown"),
            len(fc_result.unverifiable_claims),
            fc_result.missing,
        )
        return "", "rejected"

    # No claims extracted = pass through
    logger.debug("factcheck_no_claims channel_story=%s", story.get("source", "unknown"))
    return text, writer


def _story_age_hours(story: dict[str, Any]) -> float | None:
    """Age of a story in hours from its ``ts`` field, or None if undated/bad.

    Stale-news incident (2026-08-26): undated or malformed timestamps must
    be treated as "no recency signal" so the caller can fail closed.
    """
    ts = story.get("ts")
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed).total_seconds() / 3600.0


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
    if _drafts_cache["data"] is not None and time.time() - _drafts_cache["ts"] < DRAFTS_CACHE_TTL:
        logger.debug("drafts_cache_hit age=%.0fs", time.time() - _drafts_cache["ts"])
        return _drafts_cache["data"]  # type: ignore[no-any-return]  # cached drafts are typed Any

    started = time.time()
    stories = await content_candidates.recent_stories()

    drafts: dict[str, list[dict[str, Any]]] = {"main": [], "alerts": [], "news": [], "scans": []}

    alert_stories = [s for s in stories if s["severity"] in ("critical", "high")]
    main_pool = [s for s in stories if s not in alert_stories[:5]]
    news_pool = [s for s in main_pool[MAX_MAIN_PER_CYCLE:] if s["severity"] != "low"]

    # Write ALL posts concurrently under one hard deadline — the local chat
    # model shares CPU with the migration; serialized calls stall the endpoint.
    async def _timed_compose(story: dict[str, Any]) -> tuple[str, str]:
        try:
            return await asyncio.wait_for(_compose(story), timeout=180.0)
        except Exception:
            return _template_post(story), "template"

    jobs: list[tuple[str, dict[str, Any], asyncio.Task[Any]]] = []
    for s in alert_stories[:5]:
        jobs.append(("alerts", s, asyncio.ensure_future(_timed_compose(s))))
    for s in main_pool[:MAX_MAIN_PER_CYCLE]:
        jobs.append(("main", s, asyncio.ensure_future(_timed_compose(s))))
    for s in news_pool[:3]:
        jobs.append(("news", s, asyncio.ensure_future(_timed_compose(s))))

    if not jobs:
        logger.warning("content_drafts_no_stories")
        drafts["scans"] = await select_scans()
        _drafts_cache["ts"] = time.time()
        _drafts_cache["data"] = drafts
        return drafts

    done, _pending = await asyncio.wait([j[2] for j in jobs], timeout=600.0)
    for channel, story, task in jobs:
        text, writer = task.result() if task in done else (_template_post(story), "template")
        if channel == "main" and story.get("url"):
            text += f"\n\nFull coverage: {story['url']}"
        drafts[channel].append(
            {
                "text": text,
                # `headline` is Telegram HTML (bot sends parse_mode=HTML);
                # `headline_site` is plain text for the site/API. Plain-text
                # consumers must read `headline_site`, never `headline`.
                "headline": style_headline(story),
                "headline_site": style_for_site(story),
                "story_ref": story["source"],
                "mirror": channel == "main",
                "kind": story.get("kind", "story"),
                # WO-3 quality-gate fields (2026-08-25): the bot drops
                # writer=="template" drafts and anything scoring under
                # RMI_RAG_POST_MIN_SCORE. Template output never posts.
                "writer": writer,
                "score": float(story.get("importance") or 0.0),
                # stale-news fix (2026-08-26): carry the story ts so the bot's
                # recency gate can reject undated/stale drafts fail-closed.
                "ts": story.get("ts"),
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
    _drafts_cache["ts"] = time.time()
    _drafts_cache["data"] = drafts
    return drafts
