"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Message assembly for the writer and editor passes.

One place turns an edition spec + the style config into the ``$name`` values
the prompt pack expects, so the writer prompt and the editor prompt can never
disagree about the title, the banned list or the paragraph count.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from rugbrain.content.prompt_pack import prompt
from rugbrain.content_style import load_style


def prompt_values(spec: Mapping[str, Any], style: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every placeholder the edition prompts use, from the one style config."""
    rules = style or load_style()
    low, high = spec["paragraphs"]
    return {
        "edition_title": spec["title"],
        "send_utc": spec["send_utc"],
        "brief": spec["brief"],
        "skeleton": "\n".join(
            f"{index}. {heading}" for index, heading in enumerate(spec["skeleton"], 1)
        ),
        "banned": ", ".join(rules["voice"]["banned_phrases"]),
        "headline_chars": int(rules["length_budgets"]["headline_chars"]),
        "dek_chars": int(rules["length_budgets"]["dek_chars"]),
        "paragraph_min": int(low),
        "paragraph_max": int(high),
        "teaser_chars": int(spec["teaser_chars"]),
        "min_sources": int(rules["citations"]["min_sources"]),
    }


def writer_system(spec: Mapping[str, Any], style: dict[str, Any] | None = None) -> str:
    """Shared voice contract plus this edition's brief."""
    values = prompt_values(spec, style)
    return "\n\n".join([prompt("writer_system", **values), prompt(str(spec["prompt"]), **values)])


def writer_user(
    spec: Mapping[str, Any],
    notes: str,
    candidates: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The notes, the image candidates, and the order to write."""
    if candidates:
        listing = "\n".join(
            f"- {row['source_url']} | alt: {row['alt']} | credit: {row['credit']}"
            for row in candidates
        )
    else:
        listing = 'none — emit {"status": "no_image"}'
    return (
        f"NOTES — the only facts you may use:\n{notes}\n\n"
        f"IMAGE CANDIDATES — the only images you may use:\n{listing}\n\n"
        f"Write the {spec['title']} edition now. Return only the JSON object."
    )


def editor_user(
    spec: Mapping[str, Any],
    draft: Mapping[str, Any],
    notes: str,
) -> str:
    """The draft under review plus the notes it must survive."""
    payload = {
        "title": spec["title"],
        "headline": draft.get("headline", ""),
        "dek": draft.get("dek", ""),
        "paragraphs": list(draft.get("paragraphs") or ()),
        "telegram_teaser": draft.get("telegram_teaser", ""),
    }
    return (
        f"NOTES — the only facts the edition may state:\n{notes}\n\n"
        "DRAFT UNDER REVIEW:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Edit against the notes. Return only the JSON object."
    )


def editor_system(spec: Mapping[str, Any], style: dict[str, Any] | None = None) -> str:
    """The editor contract for one edition."""
    return prompt("editor_system", **prompt_values(spec, style))
