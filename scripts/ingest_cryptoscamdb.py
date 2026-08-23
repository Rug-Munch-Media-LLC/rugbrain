#!/usr/bin/env python3
"""Complete CryptoScamDB blacklist ingest into the ``known_scams`` collection.

Source: CryptoScamDB (https://cryptoscamdb.org) — an open database of
scam URLs, URIs (social accounts, onion services) and blockchain addresses
reported by the community. This script ingests the *entire* blacklist repo
(the ``data/`` directory) rather than the partial urls.yaml/uris.yaml
subset used by ``ingest_scam_knowledge.py``:

  - ``data/addresses.yaml``  — reported scam addresses with chain metadata
  - ``data/urls.yaml``       — scam URLs (websites, phishing links)
  - ``data/uris.yaml``       — scam URIs (Telegram, Twitter, onion, etc.)

Each entry is embedded (2560-dim qwen3-embedding-4b) and upserted into ``known_scams``
with canonical metadata (category, chain, severity, source, source_url).

Usage:
    python scripts/ingest_cryptoscamdb.py --dry-run
    python scripts/ingest_cryptoscamdb.py --limit 500
    python scripts/ingest_cryptoscamdb.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
  - git, PyYAML (optional; falls back to a lightweight parser)
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
logger = logging.getLogger("ingest_cryptoscamdb")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common import content_hash  # noqa: E402
from ingest_common2 import build_doc, write_docs  # noqa: E402

REPO_URL = "https://github.com/CryptoScamDB/blacklist.git"
REPO_DIR = os.getenv("CRYPTOSCAMDB_DIR", "/tmp/rag-knowledge/blacklist")  # noqa: S108  # nosec B108
COLLECTION = "known_scams"
SOURCE = "cryptoscamdb"

# Lightweight YAML field parsers (used when PyYAML is unavailable).
_YAML_KEY_RE = re.compile(r"^\s*name:\s*(.+)$")
_YAML_URL_RE = re.compile(r"^\s*url:\s*(.+)$")
_YAML_CAT_RE = re.compile(r"^\s*category:\s*(.+)$")
_YAML_SUB_RE = re.compile(r"^\s*subcategory:\s*(.+)$")
_YAML_DESC_RE = re.compile(r"^\s*description:\s*(.+)$")
_YAML_ADDR_RE = re.compile(r'^\s*-\s*"?(0x[a-fA-F0-9]{20,})"?\s*$')
_YAML_ID_RE = re.compile(r"^\s*id:\s*(.+)$")
_YAML_CHAIN_RE = re.compile(r"^\s*chain:\s*(.+)$")
_YAML_STATUS_RE = re.compile(r"^\s*status:\s*(.+)$")
_YAML_DETECTED_RE = re.compile(r"^\s*detected_at:\s*(.+)$")


def _parse_yaml_entries(text: str, source_file: str, source_url: str) -> list[dict[str, Any]]:
    """Lightweight YAML parse for CryptoScamDB list-of-dicts files."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in text.splitlines():
        m = _YAML_KEY_RE.match(line)
        if m:
            if current:
                entries.append(current)
            current = {"_file": source_file, "_url": source_url, "name": m.group(1).strip()}
            continue
        if not current:
            continue
        m = _YAML_URL_RE.match(line)
        if m:
            current["url"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_ID_RE.match(line)
        if m:
            current["id"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_CAT_RE.match(line)
        if m:
            current["category"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_SUB_RE.match(line)
        if m:
            current["subcategory"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_DESC_RE.match(line)
        if m:
            current["description"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_CHAIN_RE.match(line)
        if m:
            current["chain"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_STATUS_RE.match(line)
        if m:
            current["status"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_DETECTED_RE.match(line)
        if m:
            current["detected_at"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_ADDR_RE.match(line)
        if m:
            current.setdefault("addresses", []).append(m.group(1))
            continue
    if current:
        entries.append(current)
    return entries


def _load_file(path: Path) -> list[dict[str, Any]]:
    """Load a YAML file with PyYAML first, lightweight parser as fallback."""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    entries: list[dict[str, Any]] = []
    try:
        import yaml

        parsed = yaml.safe_load(raw)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    addr: list[str] = []
                    for _chain_name, addrs in (item.get("addresses") or {}).items():
                        if isinstance(addrs, list):
                            addr.extend(str(a) for a in addrs)
                    entries.append({**item, "_addresses": addr})
    except Exception:
        yaml = None
    if not entries:
        entries = _parse_yaml_entries(raw, path.name, "https://github.com/CryptoScamDB/blacklist")
    return entries


def _docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Build ingest docs from all data/*.yaml files in the blacklist repo."""
    data_dir = repo_root / "data"
    if not data_dir.exists():
        logger.warning("cryptoscamdb_data_dir_missing %s", data_dir)
        return []

    docs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for yaml_file in sorted(data_dir.glob("*.yaml")):
        entries = _load_file(yaml_file)
        logger.info("cryptoscamdb_file=%s entries=%d", yaml_file.name, len(entries))
        for entry in entries:
            name = str(entry.get("name", "")).strip()
            url = str(entry.get("url", "")).strip()
            category = str(entry.get("category", "")).strip()
            subcategory = str(entry.get("subcategory", "")).strip()
            description = str(entry.get("description", "")).strip()
            addresses = entry.get("_addresses") or entry.get("addresses") or []
            chain = str(entry.get("chain", "")).strip()
            if not name and not url and not addresses:
                continue

            rel_key = f"cryptoscamdb:{yaml_file.stem}:{content_hash(name + url + '|'.join(str(a) for a in addresses))}"
            if rel_key in seen:
                continue
            seen.add(rel_key)

            category_l = category.lower()
            if "phish" in category_l:
                vector = "phishing"
            elif "scam" in category_l:
                vector = "scam"
            elif "malware" in category_l:
                vector = "malware"
            elif "ponzi" in category_l or "scheme" in category_l:
                vector = "ponzi-scheme"
            else:
                vector = "unknown"
            chain = chain or ("multi" if addresses else "")

            text = (
                f"CryptoScamDB entry: {name or 'unnamed'}\n"
                f"URL: {url}\n"
                f"Category: {category} ({subcategory})\n"
                f"Chain: {chain or 'unknown'}\n"
                f"Description: {description}\n"
                f"Associated addresses: {', '.join(str(a) for a in addresses[:12])}"
            )
            severity = "critical" if (vector == "phishing" or addresses) else "high"
            metadata = {
                "title": name or url or "CryptoScamDB entry",
                "category": vector,
                "ecosystem": chain or "multi",
                "knowledge_type": "cryptoscamdb",
                "source": SOURCE,
                "source_url": url or "https://cryptoscamdb.org",
                "protocol": name,
                "attack_vector": vector,
                "scam_category": category,
                "addresses": [str(a) for a in addresses[:12]],
                "scam_url": url,
            }
            docs.append(
                build_doc(
                    source=SOURCE,
                    rel_key=rel_key,
                    content=text,
                    metadata=metadata,
                    severity=severity,
                    chain=chain or "multi",
                    collection=COLLECTION,
                )
            )
            if limit and len(docs) >= limit:
                return docs
    return docs


def _clone() -> Path:
    """Clone (shallow) the CryptoScamDB blacklist repo if not present."""
    target = Path(REPO_DIR)
    if target.exists() and (target / "data").exists():
        logger.info("cryptoscamdb_repo_present dir=%s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", REPO_URL, target)
    tmp = Path(tempfile.mkdtemp(prefix="cryptoscamdb-clone-"))
    try:
        result = subprocess.run(  # noqa: S603 — REPO_URL is a fixed constant
            ["git", "clone", "--depth", "1", REPO_URL, str(tmp)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr[-400:]}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        src_data = tmp / "data"
        if src_data.exists():
            shutil.copytree(str(src_data), str(target / "data"))
        (target / ".git").mkdir(exist_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    logger.info("cryptoscamdb_clone_complete")
    return target


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    repo_root = _clone() if not args.no_clone else Path(REPO_DIR)
    docs = _docs(repo_root, args.limit)
    logger.info("cryptoscamdb_docs=%d", len(docs))
    if not docs:
        logger.error("no cryptoscamdb entries found under %s", repo_root)
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
        description="Complete CryptoScamDB blacklist ingest into known_scams"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to ingest")
    parser.add_argument("--no-clone", action="store_true", help="Use existing clone (skip git)")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"CRYPTOSCAMDB INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
