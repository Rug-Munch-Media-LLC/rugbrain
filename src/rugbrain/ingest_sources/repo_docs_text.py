"""Heading-section text handling for the pinned-repo docs source.

One job: turn fetched repository markdown into ``(title, chunk)`` pairs, and read a
page's kind and licence from text alone. No I/O, no HTTP client, no source
registration — so the fetching module stays under the firm 200-line cap without
losing any behaviour.
"""

from __future__ import annotations

import re

MAX_SECTION_CHARS = 3000
_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")

__all__ = [
    "MAX_SECTION_CHARS",
    "chunk_sections",
    "classify_page_kind",
    "detect_licence",
    "split_sections",
]


def classify_page_kind(path: str, title: str = "") -> str:
    low = f"{path} {title}".lower()
    if any(s in low for s in ("/reference/", "api-reference", "/api/", "-api.md")):
        return "reference"
    if any(s in low for s in ("guide", "tutorial", "how-to", "quickstart", "getting-started")):
        return "guide"
    stem = path.rsplit("/", 1)[-1].lower().split(".")[0]
    if stem in ("index", "readme", "intro", "introduction", "overview", "welcome"):
        return "landing"
    return (
        "landing" if title.strip().lower() in ("introduction", "overview", "welcome") else "other"
    )


def detect_licence(text: str) -> str:
    """Best-effort SPDX id from licence text; ``UNKNOWN`` when unrecognised."""
    low = (text or "")[:4000].lower()
    table = (
        ("mit license", "MIT"),
        ("permission is hereby granted, free of charge", "MIT"),
        ("apache license", "Apache-2.0"),
        ("gnu affero", "AGPL-3.0"),
        ("gnu general public license", "GPL-3.0"),
        ("bsd 3-clause", "BSD-3-Clause"),
    )
    for needle, spdx in table:
        if needle in low:
            return spdx
    return "UNKNOWN" if low.strip() else ""


def split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title, start = "", 0
    for match in _HEADING_RE.finditer(text):
        first_line = text[match.start() :].split("\n", 1)[0]
        if text[: match.start()].count("```") % 2 == 1 or _FENCE_RE.match(first_line):
            continue
        sections.append((title, text[start : match.start()]))
        title, start = match.group(2).strip(), match.end()
    sections.append((title, text[start:]))
    return [(t, b) for t, b in sections if b.strip()]


def chunk_sections(
    sections: list[tuple[str, str]], limit: int = MAX_SECTION_CHARS
) -> list[tuple[str, str]]:
    """Pack heading sections into <=limit chunks, splitting at fence-safe breaks."""
    chunks: list[tuple[str, str]] = []
    buf: list[str] = []
    title, size = "", 0
    for name, body in sections:
        piece = f"## {name}\n{body.strip()}\n" if name else body.strip()
        if len(piece) > limit:
            if buf:
                chunks.append((title, "".join(buf)))
                buf, size = [], 0
            in_fence, part = False, []
            for line in piece.splitlines(keepends=True):
                fence = bool(_FENCE_RE.match(line))
                if (
                    len("".join(part)) + len(line) > limit
                    and not in_fence
                    and not (fence and not in_fence)
                ):
                    chunks.append((name, "".join(part)))
                    part = []
                part.append(line)
                if fence:
                    in_fence = not in_fence
            chunks.append((name, "".join(part)))
            continue
        if size + len(piece) > limit and buf:
            chunks.append((title, "".join(buf)))
            buf, size = [], 0
        if not buf:
            title = name
        buf.append(piece if piece.endswith("\n") else piece + "\n")
        size += len(piece)
    if buf:
        chunks.append((title, "".join(buf)))
    return [(t, c.strip()) for t, c in chunks if c.strip()]
