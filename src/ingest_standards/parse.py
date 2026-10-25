"""Parse EIP/ERC markdown and Solana developer docs into provenance-bearing chunks.

One job: markdown -> records. No network, no Qdrant. Code fences are never split.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
HEADING = re.compile(r"^#{2,3} ")
FENCE = re.compile(r"^\s*```")
MAX_CHARS = 3000


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    text = text.lstrip("\ufeff")
    m = FM.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, text[m.end():]


def sections(body: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    head, buf, fence = "Overview", [], False
    for line in body.splitlines():
        if FENCE.match(line):
            fence = not fence
        if not fence and HEADING.match(line):
            if buf:
                out.append((head, "\n".join(buf).strip()))
            head, buf = line.lstrip("#").strip(), [line]
            continue
        buf.append(line)
    if buf:
        out.append((head, "\n".join(buf).strip()))
    return [(h, t) for h, t in out if len(t.strip()) > 40]


def chunks(text: str) -> list[str]:
    parts, cur, size, fence = [], [], 0, False
    for line in text.splitlines():
        if FENCE.match(line):
            fence = not fence
        if not fence and size >= MAX_CHARS and not line.startswith("|"):
            parts.append("\n".join(cur).strip())
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        parts.append("\n".join(cur).strip())
    return [p for p in parts if len(p) > 40]


def eip_records(root: Path, licence: str, commit: str, cap: int = 1200) -> Iterator[dict]:
    files = sorted(root.glob("EIPS/eip-*.md")) + sorted(root.glob("ERCS/erc-*.md"))
    prio = [p for p in files if any(t in p.stem for t in ("eip-7702", "erc-4626"))]
    files = prio + [p for p in files if p not in prio]
    for path in files[:cap]:
        meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        num = meta.get("eip") or re.sub(r"\D", "", path.stem)
        kind = "erc" if path.parent.name == "ERCS" else "eip"
        for head, text in sections(body):
            for part in chunks(text):
                yield {
                    "doc_type": kind,
                    "title": meta.get("title", path.stem),
                    "eip_number": str(num),
                    "status": meta.get("status", ""),
                    "category": meta.get("category", ""),
                    "type": meta.get("type", ""),
                    "requires": meta.get("requires", ""),
                    "licence": licence,
                    "commit": commit,
                    "path": str(path.relative_to(root)),
                    "section": head,
                    "source_url": f"https://eips.ethereum.org/EIPS/{kind}-{num}",
                    "text": part,
                }


def detect_licence(root: Path) -> str:
    pkg = root / "package.json"
    if pkg.is_file():
        m = re.search(r'"license"\s*:\s*"([^"]+)"', pkg.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md"):
        p = root / name
        if p.is_file():
            head = p.read_text(encoding="utf-8", errors="replace")[:4000]
            for needle, spdx in (
                ("Apache License", "Apache-2.0"), ("MIT License", "MIT"),
                ("CC0 1.0", "CC0-1.0"), ("Creative Commons Attribution 4.0", "CC-BY-4.0"),
                ("GNU GENERAL PUBLIC LICENSE", "GPL-3.0-only"),
            ):
                if needle.lower() in head.lower():
                    return spdx
            return "SEE-LICENSE-FILE"
    return "NO-LICENSE-FILE"


def sol_records(root: Path, licence: str, commit: str, cap: int = 400) -> Iterator[dict]:
    skip = {".git", "node_modules", ".next", "build", "dist", ".docusaurus"}
    files = [p for p in root.rglob("*.md") if not (skip & set(p.relative_to(root).parts))]
    files.sort(key=lambda p: 0 if any(t in str(p).lower() for t in ("pda", "transfer-hook", "transfer_hook")) else 1)
    prio = [p for p in files if any(t in p.stem for t in ("eip-7702", "erc-4626"))]
    files = prio + [p for p in files if p not in prio]
    for path in files[:cap]:
        rel = path.relative_to(root)
        meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        for head, text in sections(body):
            for part in chunks(text):
                yield {
                    "doc_type": "solana-docs",
                    "title": meta.get("title") or rel.stem.replace("-", " "),
                    "section": head,
                    "page_path": str(rel),
                    "licence": licence,
                    "commit": commit,
                    "source_url": f"https://github.com/solana-foundation/developer-content/blob/{commit}/{rel}",
                    "text": part,
                }
