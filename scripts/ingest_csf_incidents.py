#!/usr/bin/env python3
"""Ingest the Crypto Safety First incident database into ``crime_reports``.

Source: cryptosafetyfirst/csf-crypto-incident-database — an open,
independent database of real-world crypto scams, hacks, phishing
campaigns, and user-risk incidents. Incidents are stored as CSV records
with: id, first_observed_date, incident_type, incident_subtype,
source_name, source_url, summary, user_mistake, prevention, and
classification_warning.

The CSV incident files under ``incidents/`` are parsed and embedded into
``crime_reports`` so RugBrain can answer questions about scam patterns,
user mistakes, and prevention guidance.

Usage:
    python scripts/ingest_csf_incidents.py --dry-run
    python scripts/ingest_csf_incidents.py --limit 500
    python scripts/ingest_csf_incidents.py --no-clone

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
  - git, csv (stdlib)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_csf_incidents")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common2 import write_docs  # noqa: E402

REPO_URL = "https://github.com/cryptosafetyfirst/csf-crypto-incident-database.git"
REPO_DIR = os.getenv("CSF_INCIDENTS_DIR", "/tmp/rag-csf-incidents")  # noqa: S108  # nosec B108
COLLECTION = "crime_reports"
SOURCE = "csf_incidents"

_CSV_FIELDS = (
    "id",
    "first_observed_date",
    "incident_type",
    "incident_subtype",
    "source_name",
    "source_url",
    "summary",
    "user_mistake",
    "prevention",
    "classification_warning",
)


def _clone() -> Path:
    """Shallow-clone the CSF incident database repo if not present."""
    target = Path(REPO_DIR)
    if target.exists() and (target / "incidents").exists():
        logger.info("csf_repo_present dir=%s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", REPO_URL, target)
    tmp = Path(tempfile.mkdtemp(prefix="csf-clone-"))
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
        for sub in ("incidents", "weeks", "golden_rules"):
            src_sub = tmp / sub
            if src_sub.exists():
                shutil.copytree(str(src_sub), str(target / sub))
        (target / ".git").mkdir(exist_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    logger.info("csf_repo_clone_complete")
    return target


def _row_doc(row: dict[str, str], path: Path) -> dict[str, Any] | None:
    """Build an ingest doc from one CSV row."""
    incident_id = (row.get("id") or "").strip()
    summary = (row.get("summary") or "").strip()
    if not summary:
        return None
    incident_type = (row.get("incident_type") or "unknown").strip()
    subtype = (row.get("incident_subtype") or "").strip()
    date = (row.get("first_observed_date") or "").strip()
    source_name = (row.get("source_name") or "").strip()
    source_url = (row.get("source_url") or "").strip()
    user_mistake = (row.get("user_mistake") or "").strip()
    prevention = (row.get("prevention") or "").strip()
    warning = (row.get("classification_warning") or "").strip()

    severity = "high" if incident_type in ("scam", "phishing", "wallet_drainer") else "medium"

    text = (
        f"Incident: {incident_id or 'unknown'}\n"
        f"Type: {incident_type} ({subtype})\n"
        f"First observed: {date}\n"
        f"Source: {source_name} ({source_url})\n"
        f"Summary: {summary}\n"
        f"User mistake: {user_mistake or 'n/a'}\n"
        f"Prevention: {prevention or 'n/a'}"
        + (f"\nClassification warning: {warning}" if warning else "")
    )
    metadata = {
        "title": f"CSF incident {incident_id or 'unknown'} — {incident_type}",
        "category": incident_type,
        "ecosystem": "multi",
        "knowledge_type": "csf_incident",
        "source": SOURCE,
        "source_url": source_url
        or "https://github.com/cryptosafetyfirst/csf-crypto-incident-database",
        "protocol": incident_id,
        "incident_id": incident_id,
        "incident_type": incident_type,
        "incident_subtype": subtype,
        "date": date[:10],
        "attack_vector": incident_type,
        "user_mistake": user_mistake,
        "prevention": prevention,
        "severity": severity,
    }
    return {
        "rel_key": f"csf:{incident_id or path.stem}",
        "text": text,
        "metadata": metadata,
        "severity": severity,
        "chain": "multi",
    }


def _docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Parse all incident CSVs (and week CSVs) into ingest docs."""
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in (repo_root / "incidents", repo_root / "weeks"):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.csv")):
            try:
                with path.open(encoding="utf-8", errors="ignore", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        doc = _row_doc(row, path)
                        if not doc:
                            continue
                        key = doc["rel_key"]
                        if key in seen:
                            continue
                        seen.add(key)
                        docs.append(doc)
                        if limit and len(docs) >= limit:
                            return docs
            except (OSError, csv.Error) as e:
                logger.warning("csf_csv_parse_fail file=%s err=%s", path.name, str(e)[:120])
    return docs


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    repo_root = _clone() if not args.no_clone else Path(REPO_DIR)
    docs = _docs(repo_root, args.limit)
    logger.info("csf_incidents_docs=%d", len(docs))
    if not docs:
        logger.error("no incident rows found under %s", repo_root)
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
        description="Ingest Crypto Safety First incident database into crime_reports"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max incidents to ingest")
    parser.add_argument("--no-clone", action="store_true", help="Use existing clone (skip git)")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"CSF INCIDENTS INGEST COMPLETE: {stats['inserted']} points / {stats['chunks']} chunks in {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
