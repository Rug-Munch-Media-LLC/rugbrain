"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Write one named edition from cited notes.

Same status contract as the Daily Rug long-form: ``ok`` is publishable,
``insufficient_evidence`` wrote nothing and names what is missing,
``needs_review`` wrote copy the guard or the editor still objects to. A dead
writer degrades to silence — never to template slop.

Writer and editor are injected callables (``(system, user) -> str``) so the
golden test runs the whole pipeline without a model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from rugbrain.content.draft import parse_draft
from rugbrain.content.editions import edition
from rugbrain.content.editor import editor_pass
from rugbrain.content.image import choose_image, usable_candidates
from rugbrain.content.llm import WRITER_ALIAS, chat, model_alias
from rugbrain.content.messages import writer_system, writer_user
from rugbrain.content.notes import check_evidence, normalise_notes, notes_text, source_refs
from rugbrain.content_style import load_style

WriterFn = Callable[[str, str], Awaitable[str]]


def _default_writer(model: str | None = None) -> WriterFn:
    alias = model or model_alias(WRITER_ALIAS)

    async def _call(system: str, user: str) -> str:
        return await chat(alias, system, user, max_tokens=2000, temperature=0.35)

    return _call


def _document(
    spec: Mapping[str, Any],
    day: str,
    draft: Mapping[str, Any],
    notes: Sequence[Mapping[str, Any]],
    image: Mapping[str, Any],
    rules: Mapping[str, Any],
) -> dict[str, Any]:
    """The record a site renderer consumes — no markup, no channel logic."""
    teaser = str(draft.get("telegram_teaser") or "")
    paragraphs = [str(item) for item in draft.get("paragraphs") or ()]
    return {
        "status": str(draft.get("status") or "needs_review"),
        "edition": spec["key"],
        "title": spec["title"],
        "send_utc": spec["send_utc"],
        "date": day,
        "headline": str(draft.get("headline") or ""),
        "dek": str(draft.get("dek") or ""),
        "paragraphs": paragraphs,
        "sections": [
            {"heading": heading, "paragraph": index}
            for index, heading in enumerate(spec["skeleton"][: len(paragraphs)])
        ],
        "telegram": {
            "text": teaser,
            "chars": len(teaser),
            "send_utc": spec["send_utc"],
            "limit": int(rules["length_budgets"]["telegram_hard_limit"]),
        },
        "image": dict(image),
        "sources": source_refs(notes),
        "cta": dict(rules["cta"]),
        "disclaimer": rules["disclaimers"]["daily_rug"],
        "style_violations": list(draft.get("style_violations") or ()),
        "stripped_claims": list(draft.get("stripped_claims") or ()),
        "writer": {
            "mode": "model" if paragraphs else "empty",
            "edition_prompt": spec["prompt"],
            "paragraphs": len(paragraphs),
        },
    }


def _empty(
    spec: Mapping[str, Any], day: str, status: str, reason: str, rules: Mapping[str, Any]
) -> dict[str, Any]:
    return _document(
        spec,
        day,
        {"status": status, "style_violations": [reason]},
        [],
        {"status": "no_image", "reason": "no edition written"},
        rules,
    )


async def write_edition(
    key: str,
    notes: Sequence[Mapping[str, Any]],
    *,
    date: str | None = None,
    story: Mapping[str, Any] | None = None,
    image_candidates: Sequence[Mapping[str, Any]] = (),
    writer: WriterFn | None = None,
    editor: Any = None,
    use_editor: bool = True,
    style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one edition document, or refuse it. Never raises on thin notes."""
    spec = edition(key)
    rules = style or load_style()
    day = date or datetime.now(UTC).strftime("%Y-%m-%d")
    rows = normalise_notes(notes)
    ok, reason = check_evidence(rows, int(rules["citations"]["min_sources"]))
    if not ok:
        return {
            "status": "insufficient_evidence",
            "reason": reason,
            "edition": spec["key"],
            "title": spec["title"],
            "send_utc": spec["send_utc"],
            "date": day,
            "paragraphs": [],
            "sources": source_refs(rows),
            "style_violations": [],
            "stripped_claims": [],
            "image": {"status": "no_image", "reason": "no edition written"},
            "telegram": {
                "text": "",
                "chars": 0,
                "send_utc": spec["send_utc"],
                "limit": int(rules["length_budgets"]["telegram_hard_limit"]),
            },
        }

    candidates = usable_candidates(image_candidates)
    try:
        raw = await (writer or _default_writer())(
            writer_system(spec, rules), writer_user(spec, notes_text(rows), candidates)
        )
    except Exception as exc:  # a dead writer must degrade, never block the run
        return _empty(
            spec, day, "needs_review", f"writer: unavailable ({type(exc).__name__})", rules
        )
    if not raw:
        return _empty(spec, day, "needs_review", "writer: empty completion", rules)

    draft = parse_draft(raw, spec, rows, day, rules)
    if draft["status"] == "ok" and use_editor:
        draft = await editor_pass(draft, rows, spec=spec, edit=editor, date=day, style=rules)
    subject = story or {"headline": draft.get("headline", "")}
    image = choose_image(subject, candidates, draft.get("image"))
    return _document(spec, day, draft, rows, image, rules)
