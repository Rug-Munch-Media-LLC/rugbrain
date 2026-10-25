"""Content writer — LLM pass with a clean template fallback.

Split out of :mod:`rugbrain.content_engine` (2026-10-25, size ratchet). The
prompt is rendered from the canonical pack (``config/content_prompts.json`` +
``config/content_style.json``); see ``docs/CONTENT-STYLE.md``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugbrain.formatting.headline import style_headline

logger = logging.getLogger("rugbrain.content")

CHAT_URL = os.getenv("RAG_CHAT_URL", "http://127.0.0.1:8009/v1/chat/completions")
# MUST match the alias served at CHAT_URL. :8009 (llama.service chat profile)
# serves the alias "chat"; "qwythos-9b" is the :8014 long-form alias. Pairing
# the :8009 URL with the :8014 name makes llama-server return an EMPTY
# completion with model=None - no error, just silence - which is why the bot
# produced blank posts. Both are the SAME Qwythos-9B process, two profiles.
CHAT_MODEL = os.getenv("RAG_CHAT_MODEL", "chat")
CHAT_TIMEOUT = float(os.getenv("RAG_CHAT_TIMEOUT", "45"))


#: The prompt is NOT defined here: it is rendered from the canonical pack
#: (``config/content_prompts.json`` + ``config/content_style.json``) so the
#: voice rules have one source. See ``docs/CONTENT-STYLE.md``.
def _system_prompt() -> str:
    from rugbrain.content_prompts import system_prompt

    return system_prompt("telegram_short")


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
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": notes[:9000]},
            ],
            "temperature": 0.4,
            "max_tokens": 320,
        }
        if api_key:
            # Hosted-provider mode: Bearer auth + explicit UA (gateways
            # behind Cloudflare historically block python-httpx UAs).
            headers = {
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "opencode/1.0",
            }
            # 2026-10-15: the CoT-suppression field is a DeepInfra/llama.cpp
            # extension — Groq and Gemini reject it (400 "property 'reasoning'
            # is unsupported"). Opt-in only; the Groq Qwen lane runs without it.
            if os.getenv("RAG_CHAT_REASONING_TOGGLE", "").lower() in ("1", "true", "yes"):
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
