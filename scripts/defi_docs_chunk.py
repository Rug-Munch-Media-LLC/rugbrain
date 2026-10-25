"""Heading-level chunking that never cuts inside a fenced block or a table.

Formulas and tables are the whole point of protocol mechanics docs: a chunk that
splits a table or a ```` ``` ```` block destroys the answer it was meant to carry.
Sections are cut at ATX headings outside fences; over-long sections are packed by
blank-line-separated blocks, and a block (which may be an entire table) is never
split.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_SECTION = 2600
MIN_CHUNK = 120
HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
FORMULA = re.compile(r"\$\$|\\frac|\\sqrt|\\sum|\\prod|D\s*\^|A\s*\*")
TABLE = re.compile(r"^\s*\|.+\|\s*$", re.M)


def title_of(text: str, path: str) -> str:
    match = re.search(r"^title:\s*(.+)$", text[:2000], re.M)
    if match:
        return match.group(1).strip().strip('"\'')[:160]
    match = re.search(r"^#\s+(.+)$", text, re.M)
    return (match.group(1).strip() if match else Path(path).stem.replace("-", " "))[:160]


def blocks(text: str) -> list[str]:
    """Blank-line separated blocks; blank lines INSIDE a fence do not split."""
    out: list[str] = []
    buf: list[str] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        if not fenced and not line.strip():
            if buf:
                out.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        out.append("\n".join(buf))
    return out


def pack(body: str, max_len: int = MAX_SECTION) -> list[str]:
    if len(body) <= max_len:
        return [body]
    out: list[str] = []
    current = ""
    for block in blocks(body):
        if current and len(current) + len(block) + 2 > max_len:
            out.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        out.append(current)
    return out


def sections(text: str) -> list[tuple[str, str]]:
    """Split at ATX headings outside fences. Tables/code stay with their section."""
    out: list[tuple[str, str]] = []
    name, buf, fenced = "Overview", [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        match = None if fenced else HEAD.match(line)
        if match:
            if buf:
                out.append((name, "\n".join(buf).strip()))
            name, buf = match.group(2)[:120], [line]
        else:
            buf.append(line)
    if buf:
        out.append((name, "\n".join(buf).strip()))
    return out


def chunk(protocol: str, path: str, text: str, meta: dict) -> list[dict]:
    title = title_of(text, path)
    docs: list[dict] = []
    for section, body in sections(text):
        for part in pack(body.strip()):
            if len(part) < MIN_CHUNK:
                continue
            header = f"{protocol} documentation — {title} — {section}"
            docs.append({
                "text": f"{header}\n\n{part}", "section": section, "path": path,
                "page_title": title, "has_formula": bool(FORMULA.search(part)),
                "has_table": bool(TABLE.search(part)), **meta,
            })
    return docs
