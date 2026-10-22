#!/usr/bin/env python3
"""Incremental refresh of CryptoScamDB blacklist into ``known_scams_8b``.

Wraps ``ingest_cryptoscamdb.py`` logic but adds a --since / --days filter
on the ``detected_at`` field from YAML entries, so only recent scam
entries are (re)ingested.

Source: CryptoScamDB (https://cryptoscamdb.org) — same as
``ingest_cryptoscamdb.py``.

Each entry is embedded (4096-dim Qwen3-Embedding-8B via DeepInfra) and upserted into
``known_scams_8b`` with canonical metadata (category, chain, severity, source,
source_url, detected_at).

Idempotent: point IDs are deterministic UUIDv5 from the content hash.

Usage:
    python scripts/ingest_cryptoscamdb_refresh.py --dry-run --since 2026-08-01
    python scripts/ingest_cryptoscamdb_refresh.py --days 30
    python scripts/ingest_cryptoscamdb_refresh.py  # full ingest (like original)

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - DEEPINFRA_API_KEY for the 8B lane
  - git, PyYAML (optional; falls back to a lightweight parser)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Reuse the parsing and cloning logic from the original script
from ingest_cryptoscamdb import (
    _parse_yaml_entries,
    _load_file,
    _clone,
    REPO_URL,
    REPO_DIR,
)  # noqa: F401

from ingest_common import content_hash
from ingest_common2 import build_doc
from rugbrain.embedder import get_embedder
from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_cryptoscamdb_refresh")

# Lightweight YAML field parsers (same as original)
_YAML_KEY_RE = re.compile(r"^\s*name:\s*(.+)$")
_YAML_URL_RE = re.compile(r"^\s*url:\s*(.+)$")
_YAML_CAT_RE = re.compile(r"^\s*category:\s*(.+)$")
_YAML_SUB_RE = re.compile(r"^\s*subcategory:\s*(.+)$")
_YAML_DESC_RE = re.compile(r"^\s*description:\s*(.+)$")
_YAML_ID_RE = re.compile(r"^\s*id:\s*(.+)$")
_YAML_CHAIN_RE = re.compile(r"^\s*chain:\s*(.+)$")
_YAML_STATUS_RE = re.compile(r"^\s*status:\s*(.+)$")
_YAML_DETECTED_RE = re.compile(r"^\s*detected_at:\s*(.+)$")
_YAML_ADDR_RE = re.compile(r'^\s*-\s*"?(0x[a-fA-F0-9]{20,})"?\s*$')


def _load_file_with_date(path: Path) -> list[dict[str, Any]]:
    """Load a YAML file, returning entries with a ``detected_at`` datetime."""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    entries: list[dict[str, Any]] = []
    try:
        import yaml  # type: ignore[import-untyped]

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
    # Convert detected_at string to datetime object for filtering
    for entry in entries:
        det_str = entry.get("detected_at")
        if det_str:
            try:
                # Try ISO format first
                entry["detected_at_dt"] = datetime.datetime.fromisoformat(
                    det_str.replace("Z", "+00:00")
                )
            except ValueError:
                # Fallback to flexible parsing
                try:
                    entry["detected_at_dt"] = datetime.datetime.strptime(det_str, "%Y-%m-%d")
                except ValueError:
                    entry["detected_at_dt"] = None
        else:
            entry["detected_at_dt"] = None
    return entries


def _docs_since(
    repo_root: Path, limit: int | None, since: datetime.datetime | None
) -> list[dict[str, Any]]:
    """Build ingest docs from all data/*.yaml files, filtering by detected_at."""
    data_dir = repo_root / "data"
    if not data_dir.exists():
        logger.warning("cryptoscamdb_data_dir_missing %s", data_dir)
        return []

    docs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for yaml_file in sorted(data_dir.glob("*.yaml")):
        entries = _load_file_with_date(yaml_file)
        logger.info("cryptoscamdb_file=%s entries=%d", yaml_file.name, len(entries))
        for entry in entries:
            name = str(entry.get("name", "")).strip()
            url = str(entry.get("url", "")).strip()
            category = str(entry.get("category", "")).strip()
            subcategory = str(entry.get("subcategory", "")).strip()
            description = str(entry.get("description", "")).strip()
            addresses = entry.get("_addresses") or entry.get("addresses") or []
            chain = str(entry.get("chain", "")).strip()
            detected_at_dt = entry.get("detected_at_dt")

            if not name and not url and not addresses:
                continue

            rel_key = f"cryptoscamdb:{yaml_file.stem}:{content_hash(name + url + '|'.join(str(a) for a in addresses))}"
            if rel_key in seen:
                continue
            seen.add(rel_key)

            # Date filtering
            if since is not None and detected_at_dt is not None and detected_at_dt < since:
                continue

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
                f"Associated addresses: {', '.join(str(a) for a in addresses[:12])}\n"
                f"Detected at: {detected_at_dt or 'unknown'}"
            )

            metadata = {
                "name": name,
                "url": url,
                "category": category,
                "subcategory": subcategory,
                "description": description,
                "addresses": [str(a) for a in addresses[:12]],
                "detected_at": str(detected_at_dt) if detected_at_dt else "",
                "source": "cryptoscamdb",
                "source_url": "https://github.com/CryptoScamDB/blacklist",
                "source_type": "cryptoscamdb_entry",
            }

            severity = "high" if "exploit" in category_l or "heist" in category_l else "medium"

            docs.append(
                build_doc(
                    source="cryptoscamdb",
                    rel_key=rel_key,
                    content=text,
                    metadata=metadata,
                    severity=severity,
                    chain=chain or "multi",
                    collection="known_scams_8b",
                )
            )
            if limit and len(docs) >= limit:
                return docs
    return docs


async def run(
    dry_run: bool, limit: int | None, days: int | None, since: str | None
) -> dict[str, Any]:
    stats = {
        "collection": "known_scams_8b",
        "source": "cryptoscamdb",
        "fetched": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    # Parse date filters
    since_dt: datetime.datetime | None = None
    if since is not None:
        try:
            since_dt = datetime.datetime.fromisoformat(since)
        except ValueError:
            try:
                since_dt = datetime.datetime.strptime(since, "%Y-%m-%d")
            except ValueError:
                logger.warning("invalid_since_format %s", since)
                since_dt = None
    elif days is not None and days >= 0:
        since_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)

    repo_root = _clone()
    docs = _docs_since(repo_root, limit, since_dt)
    logger.info("cryptoscamdb_docs=%d", len(docs))
    if not docs:
        logger.error("no cryptoscamdb entries found under %s", repo_root)
        return {"inserted": 0, "chunks": 0, "failed": 0}

    stored_stats = {"inserted": 0, "chunks": 0, "failed": 0}
    store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            embedder = get_embedder()
            for i, doc in enumerate(docs, start=1):
                text = (doc.get("text") or "").strip()
                if not text:
                    continue
                # Chunk text into fixed-width windows (same as ingest_common2)
                windows = []
                start = 0
                while start < len(text):
                    end = min(start + 2000, len(text))
                    chunk = text[start:end]
                    if chunk.strip():
                        windows.append(chunk)
                    if end == len(text):
                        break
                    start = max(end - 100, start + 1)

                # Embed all windows
                if windows:
                    vectors = await embedder.embed(windows)
                    for ci, (window, vector) in enumerate(zip(windows, vectors)):
                        if not vector:
                            stored_stats["failed"] += 1
                            continue
                        doc_id = f"{doc['rel_key']}:{content_hash(window)}:{ci}"
                        metadata = dict(doc.get("metadata") or {})
                        metadata["chunk_index"] = ci
                        metadata["total_chunks"] = len(windows)
                        ok = await store.insert(
                            doc_id=doc_id,
                            collection="known_scams_8b",
                            embedding=vector,
                            content=window,
                            metadata=metadata,
                            source="cryptoscamdb",
                            severity=doc.get("severity", "medium"),
                            chain=str(doc.get("chain", "") or ""),
                        )
                        if ok:
                            stored_stats["inserted"] += 1
                        else:
                            stored_stats["failed"] += 1
                        stored_stats["chunks"] += 1
                if i % 25 == 0 or i == len(docs):
                    logger.info(
                        "progress %d/%d docs, %d chunks upserted (%d failed)",
                        i,
                        len(docs),
                        stored_stats["inserted"],
                        stored_stats["failed"],
                    )
            logger.info(
                "done source=%s collection=%s docs=%d chunks=%d inserted=%d failed=%d",
                "cryptoscamdb",
                "known_scams_8b",
                len(docs),
                stored_stats["chunks"],
                stored_stats["inserted"],
                stored_stats["failed"],
            )
    finally:
        await store.close()
    # Preserve collection key in returned stats
    stats["inserted"] = stored_stats["inserted"]
    stats["chunks"] = stored_stats["chunks"]
    stats["failed"] = stored_stats["failed"]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Incremental refresh of CryptoScamDB blacklist into known_scams_8b"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max entries to ingest")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only entries detected within the last N days",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only entries detected on or after this date (YYYY-MM-DD or ISO)",
    )
    args = parser.parse_args()

    stats = asyncio.run(
        run(
            dry_run=args.dry_run,
            limit=args.limit,
            days=args.days,
            since=args.since,
        )
    )
    print("=" * 60)
    print(
        f"CRYPTOSCAMDB REFRESH COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {stats['collection']}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
