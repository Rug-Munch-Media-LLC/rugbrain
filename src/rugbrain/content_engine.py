"""Content engine — turns reranked RAG stories into channel-ready posts.

Automated content-distribution core (2026-08-24 directive):

    RAG stories (embedded, deduped, importance-reranked)
        → per-channel selection rules
        → GOOD WRITING pass (:mod:`rugbrain.content_writer`)
        → FACT-CHECK GATE (editorial standards #21, #12, #15)
        → structured drafts consumed by the bot publisher

Channel routing rules:
    main    (@cryptorugmuncher)  top 1–3 stories of the cycle, mirrored to the feed
    alerts  (@rmialerts)         critical/high severity only
    news    (@rmicryptonews)     solid news-value items, tighter format
    scans   (@rmiscans)          real scanner output (:mod:`rugbrain.content_scans`)

Quality bar: no hype words. Every post: what happened → why it matters → source.
Headline presentation lives in :mod:`rugbrain.formatting`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from rugbrain import content_candidates
from rugbrain.content_scans import SCANNER_BASE, select_scans
from rugbrain.content_writer import _compose, _template_post
from rugbrain.factcheck_gate import fact_check_gate
from rugbrain.formatting.headline import style_for_site, style_headline

logger = logging.getLogger("rugbrain.content")

__all__ = [
    "SCANNER_BASE",
    "generate_channel_drafts",
    "select_scans",
]

_drafts_cache: dict[str, Any] = {"ts": 0.0, "data": None}
DRAFTS_CACHE_TTL = int(os.getenv("RAG_DRAFTS_CACHE_TTL", "1800"))  # 30 min
MAX_MAIN_PER_CYCLE = int(os.getenv("RAG_CONTENT_MAX_MAIN", "3"))
#: Fact-check gate on the draft path (editorial standards #21, #12, #15).
#: Default ON for drafts: a draft whose verifiable claims are all
#: uncorroborated is blocked (writer="rejected"); a draft with no verifiable
#: claims passes through. Set RAG_FACTCHECK_ENABLED=0 to bypass.
FACTCHECK_ENABLED = os.getenv("RAG_FACTCHECK_ENABLED", "1").lower() not in ("0", "false", "no")


async def _compose_with_factcheck(story: dict[str, Any]) -> tuple[str, str]:
    """Compose, then run the fact-check gate (editorial standards #21/#12/#15).

    FAIL (claims present, none corroborated) blocks the draft — empty text,
    writer="rejected". PASS strips flagged claims and notes the count. SKIP
    (no verifiable claims) passes through. Bypass with RAG_FACTCHECK_ENABLED=0.
    """
    text, writer = await _compose(story)
    if not FACTCHECK_ENABLED:
        return text, writer
    fc = fact_check_gate(text, story)
    if fc.verdict == "FAIL":
        logger.warning(
            "factcheck_reject story=%s unverifiable=%d",
            story.get("source"),
            len(fc.unverifiable_claims),
        )
        return "", "rejected"
    if fc.verdict == "PASS" and fc.stripped_claims:
        text += (
            f"\n\n[Fact-check: {len(fc.stripped_claims)} claim(s) stripped — insufficient sources]"
        )
    logger.info(
        "factcheck_%s story=%s verified=%d stripped=%d",
        fc.verdict.lower(),
        story.get("source"),
        len(fc.claims),
        len(fc.stripped_claims),
    )
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
            return await asyncio.wait_for(_compose_with_factcheck(story), timeout=180.0)
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
