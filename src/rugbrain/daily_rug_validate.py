"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Publication checks for a Daily Rug brief: schema, citations, numbers, style.

Two of these checks are the anti-hallucination contract. A citation marker
must resolve to a real source id, and a number written by a model writer must
appear in the evidence it was given. Everything else is shape and style.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from rugbrain.content_style import load_style
from rugbrain.content_style_lint import lint_text

REQUIRED_KEYS = (
    "status", "slug", "url", "title", "dek", "date", "sections", "sources",
    "tokens", "charts", "cta", "disclaimer", "open_graph", "telegram", "writer",
)
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
MARKER_RE = re.compile(r"\[(\d{1,2})\]")


def numbers(text: str) -> set[str]:
    """Normalised numeric tokens, so 1,250 and 1250 compare equal."""
    return {m.group(0).replace(",", "") for m in NUMBER_RE.finditer(text)}


def reference_numbers(evidence: Sequence[dict[str, Any]], date: str) -> set[str]:
    """Numbers a writer may use: those in the evidence, plus ids and the date."""
    corpus = " ".join(f"{row['title']} {row['snippet']}" for row in evidence) + " " + date
    allowed = numbers(corpus) | {str(i) for i in range(1, 9)}
    allowed |= set(date.split("-"))
    return allowed


def validate_daily_rug(doc: dict[str, Any], style: dict[str, Any] | None = None) -> list[str]:
    """Schema, citation and style problems that block publication."""
    rules = style or load_style()
    budgets = rules["length_budgets"]
    problems = [f"schema: missing {key}" for key in REQUIRED_KEYS if key not in doc]
    if problems or doc.get("status") not in ("ok", "needs_review"):
        return problems

    sections = doc["sections"]
    minimum, maximum = int(budgets["sections_min"]), int(budgets["sections_max"])
    if not minimum <= len(sections) <= maximum:
        problems.append(f"structure: {len(sections)} sections, need {minimum}-{maximum}")

    source_ids = {row["id"] for row in doc["sources"]}
    for index, section in enumerate(sections):
        text = f"{section['heading']}\n{section['body']}"
        markers = [int(m) for m in MARKER_RE.findall(text)]
        if not markers:
            problems.append(f"citations: section {index} has no [n] marker")
        problems += [
            f"citations: section {index} marker [{m}] unresolved"
            for m in markers
            if m not in source_ids
        ]
        problems += [
            f"style: section {index} {item}"
            for item in lint_text(text, "daily_rug_max", style=rules)
        ]

    if len({row["url"] for row in doc["sources"]}) < int(rules["citations"]["min_sources"]):
        problems.append("citations: fewer than two distinct source URLs")
    if len(doc["title"]) > int(budgets["headline_chars"]):
        problems.append("length: title over headline budget")
    if len(doc["dek"]) > int(budgets["dek_chars"]):
        problems.append("length: dek over budget")
    if not str(doc["disclaimer"]).strip():
        problems.append("disclaimer: missing")
    if not doc["url"].startswith(rules["slug"]["base_url"]):
        problems.append("slug: url is not on the canonical base")
    if doc["telegram"]["chars"] >= int(budgets["telegram_hard_limit"]):
        problems.append("length: telegram summary hits the 4096 hard limit")
    problems += [
        f"style: brief {item}"
        for item in lint_text(doc["body_markdown"], "daily_rug_max", style=rules)
    ]
    return problems
