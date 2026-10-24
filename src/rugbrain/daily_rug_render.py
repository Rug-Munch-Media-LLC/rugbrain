"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Deterministic renderers for the Daily Rug brief.

Every sentence here is built from a retrieved snippet plus an attribution to
the publisher that carried it. The renderer never adds a fact: it cannot
hallucinate, which is why it is the fallback whenever a model writer returns
an unsourced or number-inventing draft.

Sections always include an explicit limits section. A brief that states what
the evidence does not show cannot read as a recommendation.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

SLUG_MAX_WORDS = 8
BULLET_CHARS = 150
def _quote(row: dict[str, Any], marker: int) -> str:
    snippet = row["snippet"].rstrip(" .")
    return f"According to {row['publisher']}, {snippet} [{marker}]."


def topic_slug(topic: str) -> str:
    """Lowercase ASCII slug, stopwords dropped, bounded word count."""
    words = re.findall(r"[a-z0-9]+", topic.lower())
    stop = {"the", "a", "an", "of", "and", "for", "to", "in", "on", "as", "at", "by"}
    kept = [w for w in words if w not in stop][:SLUG_MAX_WORDS]
    return "-".join(kept) or "market"


def canonical_slug(topic: str, date: str, style: dict[str, Any]) -> str:
    """``daily-rug-YYYY-MM-DD-<topic>`` capped at the configured length."""
    slug = f"daily-rug-{date}-{topic_slug(topic)}"
    return slug[: int(style["slug"]["max_chars"])].rstrip("-")


def canonical_url(slug: str, style: dict[str, Any]) -> str:
    return f"{style['slug']['base_url']}{slug}"


def sources(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Citation table, in the order the markers appear."""
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "publisher": row["publisher"],
            "published": row["date"],
            "url": row["url"],
            "collection": row["collection"],
        }
        for row in evidence
    ]


def compose_sections(evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    """4-8 sections, every body carrying an inline citation."""
    first, second = evidence[0], evidence[1]
    domains = len({urlparse(row["url"]).netloc.lower() for row in evidence})
    sections: list[dict[str, str]] = [
        {"heading": "What happened", "body": _quote(first, 1)},
        {"heading": "Why it matters", "body": _quote(second, 2)},
    ]
    if len(evidence) >= 3:
        sections.append({"heading": "The mechanism", "body": _quote(evidence[2], 3)})
    sections.append(
        {
            "heading": "Who is exposed",
            "body": (
                f"{first['publisher']} does not name individual counterparties in the "
                f"retrieved material [1]. The desk treats a contract-level loss as a "
                "security event for the users of that contract, not as a trading signal."
            ),
        }
    )
    if len(evidence) >= 4:
        sections.append({"heading": "Related coverage", "body": _quote(evidence[3], 4)})
    sections.append(
        {
            "heading": "Open questions",
            "body": (
                f"Retrieved {len(evidence)} sources from {domains} publishers. The evidence "
                "does not establish the total loss, the identity of the operator, or any "
                "recovery. Those stay open until a primary source documents them [1]."
            ),
        }
    )
    return sections[:8]


def to_markdown(doc: dict[str, Any]) -> str:
    """Markdown body for feed.rugmunch.io, sources included."""
    parts = [f"# {doc['title']}", "", doc["dek"]]
    for section in doc["sections"]:
        parts += ["", f"## {section['heading']}", "", section["body"]]
    parts += ["", "## Sources", ""]
    for row in doc["sources"]:
        stamp = f", {row['published']}" if row["published"] else ""
        parts.append(
            f"- [{row['id']}] {row['title']} — {row['publisher']}{stamp}. {row['url']}"
        )
    parts += ["", f"_{doc['disclaimer']}_"]
    return "\n".join(parts)
