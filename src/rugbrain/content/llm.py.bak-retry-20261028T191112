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

import os
from typing import Any

import httpx

from rugbrain.content_engine import CHAT_TIMEOUT, CHAT_URL

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
    try:
        async with httpx.AsyncClient(timeout=max(CHAT_TIMEOUT, 120.0)) as client:
            response = await client.post(CHAT_URL, headers=headers, json=payload)
            if response.status_code != 200:
                return ""
            body = response.json().get("choices", [{}])[0].get("message", {})
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return ""
    text = str(body.get("content") or "").strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    return text
