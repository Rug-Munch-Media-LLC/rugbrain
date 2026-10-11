#!/usr/bin/env python3
"""Ingest PeckShield security publications into ``contract_audits``.

Source: peckshield/publications — PeckShield's public repository of
security research, audit reports, and vulnerability disclosures. The repo
contains Markdown research notes and incident analyses covering DeFi
exploits, MEV, and contract vulnerabilities. Each Markdown document is
parsed and embedded into ``contract_audits`` so RugBrain can surface
PeckShield's technical analysis for a protocol or vulnerability class.

Usage:
    python scripts/ingest_peckshield.py --dry-run
    python scripts/ingest_peckshield.py --limit 100
    python scripts/ingest_peckshield.py --no-clone

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://127.0.0.1:8004/v1) with qwen3-embedding-8b
  - git
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_peckshield")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common import content_hash  # noqa: E402
from ingest_common2 import build_doc, write_docs  # noqa: E402

REPO_URL = "https://github.com/peckshield/publications.git"
REPO_DIR = os.getenv("PECKSHEILD_DIR", "/tmp/rag-peckshield")  # noqa: S108  # nosec B108
COLLECTION = "contract_audits"
SOURCE = "peckshield"

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$")
_SKIP_DIRS = {".git", "images", "img", "figures", "assets"}


def _clone() -> Path:
    """Shallow-clone the PeckShield publications repo if not present."""
    target = Path(REPO_DIR)
    if target.exists() and any(target.iterdir()):
        logger.info("peckshield_repo_present dir=%s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", REPO_URL, target)
    tmp = Path(tempfile.mkdtemp(prefix="peckshield-clone-"))
    try:
        result = subprocess.run(  # noqa: S603 — REPO_URL is a fixed constant
            ["git", "clone", "--depth", "1", REPO_URL, str(tmp)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr[-400:]}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for item in tmp.iterdir():
            if item.name == ".git":
                continue
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(str(item), str(dest))
            else:
                shutil.copy2(str(item), str(dest))
        (target / ".git").mkdir(exist_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    logger.info("peckshield_repo_clone_complete")
    return target


def _md_docs(path: Path, repo_root: Path) -> list[dict[str, Any]]:
    """Split a single Markdown document into heading-bounded sections."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if not raw.strip() or len(raw.strip()) < 100:
        return []

    rel = path.relative_to(repo_root).as_posix()
    sections: list[str] = []
    current: list[str] = []
    headings: list[str] = []
    title = ""
    for line in raw.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if current:
                sections.append("\n".join(current).strip())
            current = [line]
            headings.append(m.group(1).strip())
            if not title:
                title = m.group(1).strip()
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())

    docs: list[dict[str, Any]] = []
    for i, section in enumerate(sections):
        if len(section.strip()) < 60:
            continue
        vector = _detect_vector(section)
        metadata = {
            "title": f"{title or path.stem} — section {i + 1}",
            "category": vector,
            "ecosystem": "ethereum",
            "knowledge_type": "peckshield_publication",
            "source": SOURCE,
            "source_url": f"https://github.com/peckshield/publications/blob/main/{rel}",
            "protocol": title or path.stem,
            "rel_path": rel,
            "attack_vector": vector,
            "severity": "high" if vector != "unknown" else "info",
        }
        docs.append(
            build_doc(
                source=SOURCE,
                rel_key=f"peckshield:{rel}:{i}:{content_hash(section)}",
                content=section,
                metadata=metadata,
                severity=metadata["severity"],
                chain="ethereum",
                collection=COLLECTION,
            )
        )
    return docs


def _detect_vector(text: str) -> str:
    lowered = text.lower()
    for pattern, vector in (
        (r"reentrancy", "reentrancy"),
        (r"flash ?loan", "flash-loan"),
        (r"oracle", "oracle-manipulation"),
        (r"price ?manipulation", "oracle-manipulation"),
        (r"access ?control", "access-control"),
        (r"private ?key", "key-compromise"),
        (r"bridge", "bridge-exploit"),
        (r"governance", "governance-attack"),
        (r"mev", "mev"),
        (r"phish", "phishing"),
        (r"rug", "rug-pull"),
        (r"business logic", "business-logic"),
    ):
        if re.search(pattern, lowered):
            return vector
    return "unknown"


_PDF_NAME_RE = re.compile(
    r"(?P<kind>Audit-Report|Security-Review|Research|Analysis|Postmortem|Incident)"
    r"-?(?P<protocol>[\w .&-]+?)?"
    r"(?:-v\d+\.\d+)?\.pdf$",
    re.IGNORECASE,
)


def _pdf_doc(path: Path, repo_root: Path) -> dict[str, Any] | None:
    """Build a doc from a PeckShield PDF report filename + optional pdftotext.

    PDFs dominate the publications repo; the descriptive filenames carry the
    report kind and protocol. When ``pdftotext`` is available we extract the
    first pages; otherwise the filename-derived metadata is embedded.
    """
    rel = path.relative_to(repo_root).as_posix()
    name = path.stem
    m = _PDF_NAME_RE.search(name)
    kind = m.group("kind").replace("-", " ").title() if m else "Audit Report"
    protocol = (m.group("protocol") or path.parent.name).strip().title() if m else path.parent.name

    text = f"PeckShield {kind} for {protocol}."
    extracted = ""
    if shutil.which("pdftotext"):  # pragma: no cover — depends on host tools
        try:
            result = subprocess.run(  # noqa: S603
                ["pdftotext", "-l", "4", "-layout", str(path), "-"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                extracted = re.sub(r"\s+", " ", result.stdout).strip()[:4000]
        except Exception:
            extracted = ""
    if extracted:
        text += f"\nExcerpt:\n{extracted}"

    vector = _detect_vector(f"{protocol} {kind}")
    metadata = {
        "title": f"PeckShield {kind}: {protocol}",
        "category": vector,
        "ecosystem": "ethereum",
        "knowledge_type": "peckshield_publication",
        "source": SOURCE,
        "source_url": f"https://github.com/peckshield/publications/blob/main/{rel}",
        "protocol": protocol,
        "rel_path": rel,
        "attack_vector": vector,
        "report_kind": kind,
        "severity": "high" if vector != "unknown" else "info",
    }
    return build_doc(
        source=SOURCE,
        rel_key=f"peckshield:{rel}:{content_hash(name)}",
        content=text,
        metadata=metadata,
        severity=metadata["severity"],
        chain="ethereum",
        collection=COLLECTION,
    )


def _docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in sorted(repo_root.rglob("*.md")):
        rel = path.relative_to(repo_root).as_posix()
        if any(part in _SKIP_DIRS for part in rel.split("/")):
            continue
        docs.extend(_md_docs(path, repo_root))
        if limit and len(docs) >= limit:
            return docs
    # PDF reports: filename-derived docs (and pdftotext excerpts when present).
    for path in sorted(repo_root.rglob("*.pdf")):
        rel = path.relative_to(repo_root).as_posix()
        if any(part in _SKIP_DIRS for part in rel.split("/")):
            continue
        doc = _pdf_doc(path, repo_root)
        if doc:
            docs.append(doc)
        if limit and len(docs) >= limit:
            return docs
    return docs


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    repo_root = _clone() if not args.no_clone else Path(REPO_DIR)
    docs = _docs(repo_root, args.limit)
    logger.info("peckshield_docs=%d", len(docs))
    if not docs:
        logger.error("no markdown docs found under %s", repo_root)
        return {"inserted": 0, "chunks": 0, "failed": 0}

    store = QdrantVectorStore()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            stats = await write_docs(
                client,
                store,
                docs,
                source=SOURCE,
                collection=COLLECTION,
                dry_run=args.dry_run,
            )
    finally:
        await store.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest PeckShield publications into contract_audits"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max sections to ingest")
    parser.add_argument("--no-clone", action="store_true", help="Use existing clone (skip git)")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"PECKSHIELD INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
