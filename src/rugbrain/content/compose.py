"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

``compose_with_editor`` — the single-story sibling of
:func:`rugbrain.content_engine._compose_with_factcheck`.

It reuses that function rather than reimplementing it: the deterministic
fact-check gate still runs exactly once, on exactly the same story sources.
The editor pass is added on top for callers that want the second model call
before a post leaves the building, and the ``(text, writer)`` contract is
unchanged, so the swap is one call site.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from rugbrain.content.editor import editor_pass

EditFn = Callable[[str, str], Awaitable[str]]

#: A single post has no edition skeleton — one paragraph, one claim set.
_POST_SPEC: dict[str, Any] = {
    "key": "post",
    "prompt": "",
    "title": "Rug Munch desk post",
    "send_utc": "",
    "brief": "One post on the story, cited to its source.",
    "skeleton": (),
    "paragraphs": (1, 3),
    "teaser_chars": 280,
}


def _note(story: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": 1,
        "title": str(story.get("title") or story.get("headline") or "story")[:300],
        "publisher": str(story.get("source") or "unknown"),
        "snippet": str(story.get("content") or "")[:1200],
        "url": str(story.get("url") or ""),
    }


async def compose_with_editor(
    story: Mapping[str, Any],
    *,
    edit: EditFn | None = None,
    date: str = "",
    style: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Compose + fact-check + editor pass. ``writer`` is ``llm+editor`` or ``rejected``."""
    from rugbrain import content_engine  # local: keeps the package import graph flat

    text, writer = await content_engine._compose_with_factcheck(dict(story))
    if not text or writer == "rejected":
        return "", "rejected"
    draft = {
        "status": "ok",
        "headline": str(story.get("headline") or ""),
        "dek": "",
        "paragraphs": [text],
        "telegram_teaser": "",
    }
    result = await editor_pass(
        draft,
        [_note(story)],
        spec=_POST_SPEC,
        edit=edit,
        date=date,
        style=style,
        require_citations=False,
    )
    if result["status"] != "ok":
        return "", "rejected"
    return "\n\n".join(result["paragraphs"]), "llm+editor"
