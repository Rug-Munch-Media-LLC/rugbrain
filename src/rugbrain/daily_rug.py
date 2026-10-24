"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Daily Rug — the long-form brief, generated from cited RAG evidence.

Contract: ``ok`` is publishable; ``insufficient_evidence`` wrote nothing and says
why; ``needs_review`` wrote copy the style gate still objects to. Two writers:
the deterministic one only attributes retrieved snippets, so it cannot invent a
fact; a model draft is published only when every citation resolves and every
number in it appears in the evidence.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from rugbrain import daily_rug_evidence as evidence_mod
from rugbrain import daily_rug_render as render
from rugbrain.content_prompts import render as render_prompt
from rugbrain.content_prompts import system_prompt
from rugbrain.content_style import load_style
from rugbrain.content_style_lint import normalise_text
from rugbrain.daily_rug_meta import open_graph, telegram_summary
from rugbrain.daily_rug_refs import chart_refs, token_refs
from rugbrain.daily_rug_validate import MARKER_RE, numbers, reference_numbers, validate_daily_rug

WriteFn = Callable[[str, str], Awaitable[str]]
def _model_sections(raw: str, evidence: Sequence[dict[str, Any]]) -> list[dict[str, str]] | None:
    """Parse and guard one model draft. None means: do not publish it."""
    try:
        payload = json.loads(raw.strip().strip("`"))
    except (ValueError, AttributeError):
        return None
    sections = payload.get("sections")
    if not isinstance(sections, list) or not 4 <= len(sections) <= 8:
        return None
    allowed = reference_numbers(evidence, str(payload.get("date") or ""))
    ids = {row["id"] for row in evidence}
    clean: list[dict[str, str]] = []
    for section in sections:
        if not isinstance(section, dict):
            return None
        heading, body = str(section.get("heading", "")).strip(), str(section.get("body", "")).strip()
        markers = [int(m) for m in MARKER_RE.findall(body)]
        if not heading or not body or not markers or any(m not in ids for m in markers):
            return None
        if numbers(body) - allowed:
            return None
        clean.append({"heading": heading, "body": normalise_text(body)})
    return clean


async def _draft_sections(
    topic: str,
    evidence: Sequence[dict[str, Any]],
    date: str,
    writer: WriteFn | None,
    style: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    notes: dict[str, Any] = {"mode": "deterministic", "rejected": []}
    if writer is None:
        return render.compose_sections(list(evidence)), notes
    evidence_text = "\n".join(
        f"[{row['id']}] {row['title']} — {row['publisher']}. {row['snippet']} {row['url']}"
        for row in evidence
    )
    user = render_prompt(
        "daily_rug",
        topic=topic,
        evidence=evidence_text,
        date=date,
        primary_url=evidence[0]["url"],
        style=style,
    )
    try:
        sections = _model_sections(await writer(system_prompt("daily_rug", style), user), evidence)
    except Exception as exc:  # a dead writer must degrade, never block the brief
        sections = None
        notes["rejected"].append(f"writer_error: {type(exc).__name__}")
    if sections is None:
        notes["rejected"].append("model draft failed citation or number guard")
        return render.compose_sections(list(evidence)), notes
    notes["mode"] = "model"
    return sections, notes


async def generate_daily_rug(
    topic: str,
    *,
    date: str | None = None,
    search_fn: evidence_mod.SearchFn | None = None,
    writer: WriteFn | None = None,
    style: dict[str, Any] | None = None,
    min_sources: int | None = None,
) -> dict[str, Any]:
    """Build one Daily Rug brief, or return ``insufficient_evidence``."""
    rules = style or load_style()
    day = date or datetime.now(UTC).strftime("%Y-%m-%d")
    floor = int(min_sources or rules["citations"]["min_sources"])
    evidence = await evidence_mod.collect_evidence(topic, search_fn=search_fn)
    ok, reason = evidence_mod.sufficiency(evidence, floor)
    if not ok:
        return {
            "status": "insufficient_evidence",
            "reason": reason,
            "topic": topic,
            "date": day,
            "retrieved": len(evidence),
        }

    sections, writer_notes = await _draft_sections(topic, evidence, day, writer, rules)
    slug = render.canonical_slug(topic, day, rules)
    domains = len({urlparse(row["url"]).netloc.lower() for row in evidence})
    document: dict[str, Any] = {
        "status": "ok",
        "topic": topic,
        "slug": slug,
        "url": render.canonical_url(slug, rules),
        "title": evidence[0]["title"][: int(rules["length_budgets"]["headline_chars"])].rstrip(" ."),
        "dek": f"The brief draws on {len(evidence)} sources from {domains} publishers.",
        "date": day,
        "sections": sections,
        "sources": render.sources(list(evidence)),
        "tokens": token_refs(evidence),
        "charts": chart_refs(evidence),
        "cta": dict(rules["cta"]),
        "disclaimer": rules["disclaimers"]["daily_rug"],
        "open_graph": {},
        "telegram": {},
        "writer": writer_notes,
    }
    document["open_graph"] = open_graph(document, rules)
    summary = telegram_summary(document, rules)
    document["telegram"] = {
        "text": summary,
        "chars": len(summary),
        "url": document["url"],
        "limit": int(rules["length_budgets"]["telegram_hard_limit"]),
    }
    document["body_markdown"] = render.to_markdown(document)
    problems = validate_daily_rug(document, rules)
    if problems:
        document["status"] = "needs_review"
        document["style_violations"] = problems
    return document
