"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

The one chat call the edition pipeline makes — endpoint and auth shared with
the content engine, model chosen by the caller.

:8009 serves the presets ``writer`` and ``editor`` (``/etc/llama/presets.ini``,
same Qwythos-9B process, 65536 context). Sending a preset alias that the server
does not serve returns an EMPTY completion with ``model=None`` — no error —
so the alias is always a named constant here, never improvised at a call site.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from rugbrain.content_engine import CHAT_TIMEOUT, CHAT_URL

log = logging.getLogger(__name__)

WRITER_ALIAS = "writer"
EDITOR_ALIAS = "editor"


def model_alias(kind: str = WRITER_ALIAS) -> str:
    """Alias for ``writer`` or ``editor``, overridable for a hosted lane."""
    env = "RAG_EDITION_EDITOR_MODEL" if kind == EDITOR_ALIAS else "RAG_EDITION_WRITER_MODEL"
    return os.getenv(env, "").strip() or kind


async def chat(
    model: str,
    system: str,
    user: str,
    *,
    max_tokens: int = 1400,
    temperature: float = 0.3,
) -> str:
    """One completion. Empty string on any failure — callers fail closed."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"User-Agent": "rugbrain-editions/1.0"}
    api_key = os.getenv("RAG_CHAT_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # A cold Qwythos preset reload is ~7 G and can take minutes under load, so
    # the writer timeout is explicit and longer than the chat default. One retry
    # covers the reload window; a second failure is a real refusal.
    timeout = float(os.getenv("RAG_EDITION_TIMEOUT", "300"))
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=max(CHAT_TIMEOUT, timeout)) as client:
                response = await client.post(CHAT_URL, headers=headers, json=payload)
                if response.status_code != 200:
                    log.warning(
                        "edition_chat_http", model=model, status=response.status_code,
                        attempt=attempt,
                    )
                    continue
                body = response.json().get("choices", [{}])[0].get("message", {})
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            log.warning("edition_chat_failed", model=model, error=str(exc)[:120],
                        attempt=attempt)
            continue
        text = str(body.get("content") or "").strip()
        if text:
            break
        log.warning("edition_chat_empty", model=model, attempt=attempt)
        if attempt == 1:
            await asyncio.sleep(5)
    else:
        return ""
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    return text
