"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

The editor pass — a second model call that removes what the notes do not
support, then re-runs the deterministic guard on whatever it returns.

Sibling of the content engine's fact-check path, not a copy of it: this is the
*prose* gate for a full edition, and it returns ``style_violations`` alongside
the edited copy. Its output still has to survive :func:`guard_copy` and
:func:`lint_text`, so an editor that hallucinates a fix cannot publish one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from rugbrain.content.draft import guard_copy, load_json
from rugbrain.content.llm import EDITOR_ALIAS, chat, model_alias
from rugbrain.content.messages import editor_system, editor_user
from rugbrain.content.notes import normalise_notes, notes_text
from rugbrain.content_style import load_style
from rugbrain.content_style_lint import lint_text

EditFn = Callable[[str, str], Awaitable[str]]


def _default_editor(model: str | None = None) -> EditFn:
    alias = model or model_alias(EDITOR_ALIAS)

    async def _call(system: str, user: str) -> str:
        return await chat(alias, system, user, max_tokens=1600, temperature=0.1)

    return _call


def _verdict(payload: Mapping[str, Any], draft: Mapping[str, Any]) -> str:
    stated = str(payload.get("verdict") or "").strip().lower()
    if stated in ("ok", "needs_review", "insufficient_evidence"):
        return stated
    return str(draft.get("status") or "needs_review")


async def editor_pass(
    draft: Mapping[str, Any],
    notes: Sequence[Mapping[str, Any]],
    *,
    spec: Mapping[str, Any],
    edit: EditFn | None = None,
    model: str | None = None,
    date: str = "",
    style: dict[str, Any] | None = None,
    require_citations: bool = True,
) -> dict[str, Any]:
    """Fact-check, strip and style-gate one draft. Always returns violations."""
    rules = style or load_style()
    rows = normalise_notes(list(notes))
    result: dict[str, Any] = {
        "status": str(draft.get("status") or "needs_review"),
        "headline": str(draft.get("headline") or ""),
        "dek": str(draft.get("dek") or ""),
        "paragraphs": list(draft.get("paragraphs") or ()),
        "telegram_teaser": str(draft.get("telegram_teaser") or ""),
        "style_violations": list(draft.get("style_violations") or ()),
        "stripped_claims": list(draft.get("stripped_claims") or ()),
    }
    if result["status"] != "ok":
        return result

    system = editor_system(spec, rules)
    user = editor_user(spec, draft, notes_text(rows))
    try:
        raw = await (edit or _default_editor(model))(system, user)
    except Exception as exc:  # a dead editor degrades to the deterministic gate
        raw = ""
        result["style_violations"].append(f"editor: unavailable ({type(exc).__name__})")
    payload = load_json(raw) if raw else {}
    if raw and not payload:
        result["style_violations"].append("editor: response was not a JSON object")

    if payload:
        kept, violations, stripped = guard_copy(
            [str(item) for item in payload.get("paragraphs") or () if isinstance(item, str)],
            rows,
            date,
        )
        if kept:
            result["paragraphs"] = kept
        elif payload.get("paragraphs"):
            result["style_violations"].append("editor: every edited paragraph failed the guard")
        result["style_violations"] += violations
        result["stripped_claims"] += [str(item) for item in payload.get("stripped_claims") or ()]
        result["stripped_claims"] += stripped
        result["style_violations"] += [
            f"editor: {item!s}" for item in payload.get("style_violations") or ()
        ]
        for key in ("headline", "dek", "telegram_teaser"):
            if isinstance(payload.get(key), str) and payload[key].strip():
                result[key] = payload[key].strip()
        result["status"] = _verdict(payload, result)

    body = "\n\n".join(result["paragraphs"])
    if body:
        sources = len(rows) if require_citations else None
        result["style_violations"] += lint_text(
            body, "feed_longform", style=rules, source_count=sources
        )
    seen: list[str] = []
    for violation in result["style_violations"]:
        if violation not in seen:
            seen.append(violation)
    result["style_violations"] = seen
    if result["status"] == "ok" and seen:
        result["status"] = "needs_review"
    return result
