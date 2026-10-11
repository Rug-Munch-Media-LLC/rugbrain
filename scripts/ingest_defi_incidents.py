#!/usr/bin/env python3
"""Ingest the DeFi Security Incident Database into ``defi_exploits``.

Source: kismp123/DeFi-Security-Incident — 800+ real-world DeFi security
incidents (2020-2026) with root-cause analysis, vulnerable-code analysis,
attack-flow diagrams, on-chain source, and Foundry PoC exploits, organized
by year and vulnerability type.

Each incident markdown file is parsed (front-matter-style metadata table +
sections) and embedded into ``defi_exploits`` so RugBrain can answer
'how was protocol X exploited and what was the root cause?'.

Usage:
    python scripts/ingest_defi_incidents.py --dry-run
    python scripts/ingest_defi_incidents.py --limit 100
    python scripts/ingest_defi_incidents.py --year 2022

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
logger = logging.getLogger("ingest_defi_incidents")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common import content_hash  # noqa: E402
from ingest_common2 import write_docs  # noqa: E402

REPO_URL = "https://github.com/kismp123/DeFi-Security-Incident.git"
REPO_DIR = os.getenv("DEFI_INCIDENTS_DIR", "/tmp/rag-defi-incidents")  # noqa: S108  # nosec B108
COLLECTION = "defi_exploits"
SOURCE = "defi_incidents"

_META_KEY_RE = re.compile(r"^\|\s*\*\*([^*]+)\*\*\s*\|\s*(.+?)\s*\|\s*$")
_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$")
_FIELD_MAP = {
    "date": "date",
    "protocol": "protocol",
    "chain": "chain",
    "loss": "loss",
    "attacker": "attacker",
    "attack tx": "attack_tx",
    "root cause": "root_cause",
    "vulnerable contract": "vulnerable_contract",
    "poc source": "poc_source",
}


def _clone() -> Path:
    """Shallow-clone the incident database repo if not present."""
    target = Path(REPO_DIR)
    if target.exists() and any(p.is_dir() and p.name.isdigit() for p in target.iterdir()):
        logger.info("incident_repo_present dir=%s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", REPO_URL, target)
    tmp = Path(tempfile.mkdtemp(prefix="defi-incidents-clone-"))
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
    logger.info("incident_repo_clone_complete")
    return target


def _parse_incident(path: Path) -> dict[str, Any] | None:
    """Parse one incident markdown file into (metadata, text)."""
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if not raw.strip():
        return None

    fields: dict[str, str] = {}
    body_lines: list[str] = []
    in_meta = False
    for line in raw.splitlines():
        m = _META_KEY_RE.match(line)
        if m:
            in_meta = True
            key = m.group(1).strip().lower()
            value = m.group(2).strip()
            canonical = _FIELD_MAP.get(key)
            if canonical:
                fields[canonical] = value
            continue
        if in_meta and line.strip().startswith("|"):
            continue
        if in_meta and not line.strip().startswith("|") and not line.strip():
            continue
        in_meta = False
        body_lines.append(line)

    protocol = fields.get("protocol", path.stem)
    date = fields.get("date", "")
    chain = fields.get("chain", "")
    loss = fields.get("loss", "")
    root_cause = fields.get("root_cause", "")
    attack_tx = fields.get("attack_tx", "")
    poc = fields.get("poc_source", "")

    body = "\n".join(body_lines).strip()
    if not body:
        body = root_cause or f"{protocol} security incident ({date or 'unknown date'})."

    vector = _detect_vector(f"{protocol} {root_cause} {body[:600]}")
    amount = _parse_loss(loss)
    text = (
        f"Protocol: {protocol}\n"
        f"Date: {date}\n"
        f"Chain: {chain}\n"
        f"Loss: {loss}\n"
        f"Root cause: {root_cause}\n"
        f"Attack tx: {attack_tx}\n"
        f"PoC source: {poc}\n"
        f"Analysis:\n{body[:12000]}"
    )
    metadata = {
        "source_type": SOURCE,
        "protocol": protocol,
        "date": date[:10] if date else "",
        "date_ts": _date_to_ts(date),
        "chain": chain or "multi",
        "attack_vector": vector,
        "amount_lost_usd": amount,
        "root_cause": root_cause[:2000],
        "severity": "critical" if amount >= 1e7 else ("high" if amount else "medium"),
        "source_url": poc or "https://github.com/kismp123/DeFi-Security-Incident",
        "rel_path": path.relative_to(path.parent.parent).as_posix(),
    }
    return {
        "rel_key": f"kismp:{path.stem}:{content_hash(text)}",
        "text": text,
        "metadata": metadata,
        "severity": metadata["severity"],
        "chain": chain or "multi",
    }


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
        (r"signature", "signature-malleability"),
        (r"overflow|underflow", "integer-overflow"),
        (r"phish", "phishing"),
        (r"rug", "rug-pull"),
        (r"business logic", "business-logic"),
        (r"donation", "donation-inflation"),
        (r"approval", "approval-abuse"),
        (r"arbitrary call", "arbitrary-call"),
    ):
        if re.search(pattern, lowered):
            return vector
    return "unknown"


def _parse_loss(raw: str) -> float:
    """Parse a loss string like '$11,000,000' or '~$18.8M' to float USD."""
    if not raw:
        return 0.0
    cleaned = raw.replace(",", "").replace("$", "").replace("~", "").strip()
    m = re.match(r"([\d.]+)\s*([kmb]?)$", cleaned, re.IGNORECASE)
    if not m:
        return 0.0
    value = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    elif suffix == "b":
        value *= 1_000_000_000
    return value


def _date_to_ts(date_iso: str) -> int:
    import datetime as _dt

    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_iso or "")
    if not m:
        return 0
    try:
        return int(
            _dt.datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=_dt.UTC
            ).timestamp()
        )
    except ValueError:
        return 0


def _docs(repo_root: Path, limit: int | None, year: str | None) -> list[dict[str, Any]]:
    """Walk the year directories and parse every incident markdown."""
    docs: list[dict[str, Any]] = []
    if year:
        dirs = [repo_root / year]
    else:
        dirs = sorted(d for d in repo_root.iterdir() if d.is_dir() and d.name.isdigit())
    for d in dirs:
        if not d.exists():
            continue
        for path in sorted(d.glob("*.md")):
            doc = _parse_incident(path)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                return docs
    return docs


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    repo_root = _clone() if not args.no_clone else Path(REPO_DIR)
    docs = _docs(repo_root, args.limit, args.year)
    logger.info("defi_incidents_docs=%d", len(docs))
    if not docs:
        logger.error("no incident files found under %s (year=%s)", repo_root, args.year)
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
        description="Ingest DeFi Security Incident Database into defi_exploits"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max incidents to ingest")
    parser.add_argument(
        "--year", default=None, help="Only ingest incidents from this year (e.g. 2022)"
    )
    parser.add_argument("--no-clone", action="store_true", help="Use existing clone (skip git)")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"DEFI INCIDENTS INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
