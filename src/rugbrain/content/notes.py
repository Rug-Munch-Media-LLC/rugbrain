"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Notes → prompt evidence for the editions.

A note row is the Daily Rug evidence shape (``id``/``title``/``publisher``/
``snippet``/``url``) so the citation guard, the number guard and the image
credit all read one normalised structure. Rows without an http URL are dropped:
an uncitable note is not a source.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from rugbrain.daily_rug_evidence import sufficiency


def normalise_notes(rows: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Renumber notes 1..n, keep only citable rows, fill every key."""
    notes: list[dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        url = str(row.get("url") or row.get("source_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        notes.append(
            {
                "id": len(notes) + 1,
                "title": str(row.get("title") or url)[:300],
                "publisher": str(row.get("publisher") or urlparse(url).netloc),
                "snippet": str(row.get("snippet") or row.get("content") or "")[:1200],
                "url": url,
            }
        )
    return notes


def notes_text(notes: Sequence[Mapping[str, Any]]) -> str:
    """The numbered list the writer may cite — nothing outside it is a fact."""
    return "\n".join(
        f"[{row['id']}] {row['title']} — {row['publisher']}. {row['snippet']} {row['url']}"
        for row in notes
    )


def source_refs(notes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Source list for the rendered document (citation → URL)."""
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "publisher": row["publisher"],
            "url": row["url"],
        }
        for row in notes
    ]


def check_evidence(notes: Sequence[Mapping[str, Any]], min_sources: int = 2) -> tuple[bool, str]:
    """Two independent publishers or no edition — the Daily Rug floor, reused."""
    return sufficiency(notes, max(2, int(min_sources)))
