"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Image selection contract — one image, from the candidate list only.

A candidate is usable when it carries an http(s) ``source_url``, an ``alt``
text and a ``credit``. The writer may propose one (its JSON ``image`` field);
the proposal is honoured only when its URL is literally in the candidate list,
and the alt/credit are taken from the candidate, never from the model. Nothing
usable means ``no_image`` — never a placeholder, never a stock URL.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_WORD_RE = re.compile(r"[a-z0-9]{4,}")


def usable_candidates(
    candidates: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    """Candidate rows that satisfy all three credit fields, in input order."""
    rows: list[dict[str, str]] = []
    for row in candidates or ():
        if not isinstance(row, Mapping):
            continue
        url = str(row.get("source_url") or row.get("url") or "").strip()
        alt = str(row.get("alt") or "").strip()
        credit = str(row.get("credit") or "").strip()
        if not url.startswith(("http://", "https://")) or not alt or not credit:
            continue
        rows.append({"source_url": url, "alt": alt, "credit": credit})
    return rows


def _tokens(value: str) -> set[str]:
    return set(_WORD_RE.findall(value.lower()))


def _score(row: Mapping[str, str], wanted: set[str]) -> int:
    return len(_tokens(f"{row['alt']} {row['credit']}") & wanted)


def choose_image(
    story: Mapping[str, Any] | None,
    candidates: Sequence[Mapping[str, Any]] | None = (),
    proposed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pick one candidate: honour the writer's proposal, else rank by overlap.

    Returns ``{"status": "ok", "source_url", "alt", "credit", "matched"}`` or
    ``{"status": "no_image", "reason"}``.
    """
    rows = usable_candidates(candidates)
    if not rows:
        return {"status": "no_image", "reason": "no candidate carried url + alt + credit"}

    if isinstance(proposed, Mapping) and proposed.get("source_url"):
        wanted_url = str(proposed.get("source_url")).strip()
        for row in rows:
            if row["source_url"] == wanted_url:
                return {"status": "ok", **row, "matched": "proposed"}

    subject = _tokens(
        f"{story.get('title', '') if story else ''} "
        f"{story.get('headline', '') if story else ''} "
        f"{story.get('summary', '') if story else ''}"
    )
    ranked = sorted(
        enumerate(rows),
        key=lambda pair: (_score(pair[1], subject), -pair[0]),
        reverse=True,
    )
    return {"status": "ok", **ranked[0][1], "matched": "candidate"}
