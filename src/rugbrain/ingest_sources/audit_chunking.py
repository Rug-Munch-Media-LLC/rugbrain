"""Finding-level chunking for the security knowledge ingest.

Split by FINDING heading, pack outside fenced code blocks. Kept as its own
module so the ingest script stays an entry point and the splitter is testable.
"""

from __future__ import annotations

import re
from typing import Final

MAX_CHARS: Final = 1800

_HEADING = re.compile(r"^(#{1,4})\s+(?!#)(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_FINDING = re.compile(
    r"(?i)^(?:\[)?(C|H|M|L|G|I|N|QA|R|NC)\s?[--]?\s?(\d{1,3})[\]:.\s—-]|"
    r"^(critical|high|medium|low|informational|gas|non-critical)\b"
)
_SEVERITY = {"C": "critical", "H": "high", "M": "medium", "L": "low", "G": "low",
             "I": "info", "N": "info", "QA": "info", "R": "info", "NC": "info"}


def _severity_of(heading: str) -> str:
    m = _FINDING.match(heading.strip().lstrip("# "))
    if not m:
        return "info"
    if m.group(1):
        return _SEVERITY.get(m.group(1).upper(), "info")
    return {"critical": "critical", "high": "high", "medium": "medium", "low": "low",
            "informational": "info", "gas": "low", "non-critical": "info"}.get(m.group(0).lower().strip(), "info")


def _units(text: str) -> list[tuple[str, str, str]]:
    """Split *text* into ``(finding_id, title, body)`` units, fence-aware."""
    units: list[tuple[str, str, list[str]]] = [("context", "", [])]
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            units[-1][2].append(line)
            continue
        m = None if in_fence else _HEADING.match(line)
        fm = _FINDING.match(m.group(2)) if m else None
        if fm:
            title = m.group(2).strip()
            # The SEVERITY token is the identity of a finding (H-01), and it is
            # the only stable key across a re-published report; the heading level
            # is presentation and must never become part of the id.
            token = fm.group(1) or title.split()[0]
            digits = re.sub(r"\D", "", fm.group(2) or re.sub(r"\D", "", title.split()[0]))
            fid = f"{token.upper()}{digits}" if digits else f"F{len(units)}"
            units.append((fid, title, []))
            continue
        units[-1][2].append(line)
    out = [(fid, title, "\n".join(body).strip()) for fid, title, body in units]
    # A finding with a heading and no body still answers "which issues exist in
    # this contest"; the leading CONTEXT unit is dropped only when empty.
    return [u for u in out if u[2] or (u[1] and u[0] != "context")]


def _pack(body: str) -> list[str]:
    """Pack *body* into <= MAX_CHARS pieces, cutting only outside fences."""
    if len(body) <= MAX_CHARS:
        return [body]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    in_fence = False
    for line in body.splitlines(keepends=True):
        if _FENCE.match(line):
            # A fence marker always joins the block it delimits: cutting before
            # the CLOSING fence would emit a piece with an unclosed block, which
            # is the exact tear this packer exists to prevent.
            in_fence = not in_fence
            current.append(line)
            size += len(line)
            continue
        if size + len(line) > MAX_CHARS and current and not in_fence:
            pieces.append("".join(current).strip())
            current, size = [], 0
        current.append(line)
        size += len(line)
    if "".join(current).strip():
        pieces.append("".join(current).strip())
    return [p for p in pieces if p]
