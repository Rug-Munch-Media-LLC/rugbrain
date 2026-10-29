"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

The guard every model draft must survive before it is a document.

Two checks are the anti-hallucination contract, reused from the Daily Rug
validator rather than reimplemented: a ``[n]`` marker must resolve to a note
id, and a figure must appear in the notes (or be the edition date). Everything
else is shape — paragraph count, headline and teaser caps.

A paragraph that fails either check is dropped and named. Copy is never
"softened" into a claim no note supports.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from rugbrain.content_style import load_style
from rugbrain.content_style_normalise import normalise_text
from rugbrain.daily_rug_validate import MARKER_RE, numbers, reference_numbers


def load_json(raw: str) -> dict[str, Any]:
    """Parse one JSON object; tolerate a fenced block the model added anyway."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def guard_copy(
    paragraphs: Sequence[Any],
    notes: Sequence[Mapping[str, Any]],
    date: str,
) -> tuple[list[str], list[str], list[str]]:
    """``(kept, violations, stripped)`` for candidate paragraphs."""
    allowed = reference_numbers(notes, date) | {str(index) for index in range(1, len(notes) + 1)}
    ids = {int(row["id"]) for row in notes}
    kept: list[str] = []
    violations: list[str] = []
    stripped: list[str] = []
    for index, raw in enumerate(paragraphs, 1):
        text = normalise_text(str(raw)) if isinstance(raw, str) else ""
        if not text:
            continue
        markers = [int(match) for match in MARKER_RE.findall(text)]
        if not markers:
            violations.append(f"citations: paragraph {index} has no [n] marker")
            stripped.append(text)
            continue
        missing = sorted({marker for marker in markers if marker not in ids})
        if missing:
            violations.append(f"citations: paragraph {index} cites missing note(s) {missing}")
            stripped.append(text)
            continue
        invented = sorted(numbers(text) - allowed)
        if invented:
            violations.append(
                f"numbers: paragraph {index} states figures absent from the notes {invented}"
            )
            stripped.append(text)
            continue
        kept.append(normalise_text(text))
    return kept, violations, stripped


def _clip(text: str, cap: int, field: str) -> tuple[str, list[str]]:
    if len(text) <= cap:
        return text, []
    cut = text[:cap].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return cut, [f"length: {field} truncated to {cap} characters"]


def parse_draft(
    raw: str,
    spec: Mapping[str, Any],
    notes: Sequence[Mapping[str, Any]],
    date: str,
    style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one writer completion into an edition draft."""
    rules = style or load_style()
    payload = load_json(raw)
    status = str(payload.get("status") or "").strip().lower()
    if status == "insufficient_evidence":
        return {
            "status": "insufficient_evidence",
            "reason": str(payload.get("reason") or "writer refused: evidence too thin"),
            "paragraphs": [],
            "style_violations": [],
            "stripped_claims": [],
        }
    if status != "ok":
        return {
            "status": "needs_review",
            "paragraphs": [],
            "style_violations": ["format: writer did not return status ok"],
            "stripped_claims": [],
        }

    paragraphs = payload.get("paragraphs")
    if not isinstance(paragraphs, list):
        return {
            "status": "needs_review",
            "paragraphs": [],
            "style_violations": ["format: paragraphs must be a list"],
            "stripped_claims": [],
        }
    kept, violations, stripped = guard_copy(paragraphs, notes, date)
    low, high = (int(bound) for bound in spec["paragraphs"])
    if not low <= len(kept) <= high:
        violations.append(f"structure: {len(kept)} usable paragraph(s), edition needs {low}-{high}")

    budgets = rules["length_budgets"]
    headline, headline_notes = _clip(
        str(payload.get("headline") or "").strip().rstrip("."),
        int(budgets["headline_chars"]),
        "headline",
    )
    if not headline:
        violations.append("structure: headline is empty")
    dek, dek_notes = _clip(str(payload.get("dek") or "").strip(), int(budgets["dek_chars"]), "dek")
    teaser, teaser_notes = _clip(
        str(payload.get("telegram_teaser") or "").strip(), int(spec["teaser_chars"]), "teaser"
    )
    if MARKER_RE.search(teaser):
        violations.append("citations: teaser carries [n] markers")
    violations += headline_notes + dek_notes + teaser_notes

    return {
        "status": "ok" if not violations else "needs_review",
        "headline": headline,
        "dek": dek,
        "paragraphs": kept,
        "telegram_teaser": teaser,
        "image": payload.get("image") if isinstance(payload.get("image"), Mapping) else {},
        "style_violations": violations,
        "stripped_claims": stripped,
    }
