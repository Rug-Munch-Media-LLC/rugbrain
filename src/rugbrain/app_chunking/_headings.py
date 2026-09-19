"""Heading detection and sectioning (markdown / reST / AsciiDoc).

Headings become section boundaries; each chunk carries the active section
heading as context and metadata.
"""

from __future__ import annotations

import re

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)
_RST_HEADING_RE = re.compile(r"^([^\n]{2,60})\n[=\-~^+*]{3,}\s*$", re.MULTILINE)
_ADOC_HEADING_RE = re.compile(r"^([=]{1,5}|[-]{1,5})\s+(.*?)\s*$", re.MULTILINE)


def _heading_body_start(text: str, heading_start: int, kind: str) -> int:
    """Position just after the heading syntax (its line, + underline for reST)."""
    nl = text.find("\n", heading_start)
    if nl == -1:
        return len(text)
    if kind == "rst":
        nl2 = text.find("\n", nl + 1)
        return len(text) if nl2 == -1 else nl2 + 1
    return nl + 1


def _detect_headings(text: str) -> list[tuple[int, str, str, int]]:
    """Return ``(pos, label, marker, body_start)`` for headings.

    ``label`` is the plain heading text; ``marker`` is the original syntax
    (``##``, ``=`` …) used to rebuild the heading line as chunk context;
    ``body_start`` is where the section body begins (past the heading line).
    """
    out: list[tuple[int, str, str, int]] = []
    for m in _MD_HEADING_RE.finditer(text):
        label = m.group(2).strip()
        if label:
            out.append((m.start(), label, m.group(1), _heading_body_start(text, m.start(), "md")))
    for m in _RST_HEADING_RE.finditer(text):
        if any(abs(m.start() - pos) < 2 for pos, _, _, _ in out):
            continue
        # reST headings carry no inline marker; the text is the heading line.
        out.append((m.start(), m.group(1).strip(), "", _heading_body_start(text, m.start(), "rst")))
    for m in _ADOC_HEADING_RE.finditer(text):
        label = m.group(2).strip()
        if label and not any(abs(m.start() - pos) < 2 for pos, _, _, _ in out):
            out.append(
                (m.start(), label, m.group(1)[0], _heading_body_start(text, m.start(), "adoc"))
            )
    out.sort(key=lambda t: t[0])
    deduped: list[tuple[int, str, str, int]] = []
    for pos, label, marker, body_start in out:
        if deduped and abs(pos - deduped[-1][0]) < 2:
            continue
        deduped.append((pos, label, marker, body_start))
    return deduped


def _sectionize(text: str, title: str = "") -> list[tuple[str, str, str]]:
    """Split *text* into ``(section_body, heading_context, heading_label)``.

    When the document has headings, each section is chunked independently so
    content from different sections is never mixed in one chunk. The section
    body starts *after* the heading line; ``heading_context`` (the rebuilt
    heading line, e.g. ``# Audit``) is prepended to every chunk so each one
    carries its section context. Documents without headings are a single
    section whose context is the title.
    """
    headings = _detect_headings(text)
    if not headings:
        return [(text, title, title)]
    sections: list[tuple[str, str, str]] = []
    for i, (_pos, label, marker, body_start) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        sec = text[body_start:end]
        if sec.strip():
            sections.append((sec, f"{marker} {label}".strip(), label))
    if headings[0][0] > 0 and text[: headings[0][0]].strip():
        sections.insert(0, (text[: headings[0][0]], title, title))
    return sections
