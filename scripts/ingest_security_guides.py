#!/usr/bin/env python3
"""Ingest security-audit guides into the RugBrain ``coding_knowledge`` collection.

Part 4 of the coding-knowledge expansion. Merges curated guides with
live-fetched audit references:

  - Solcurity (Rari-Capital/solcurity) — Solidity security checklist.
  - Consensys Smart Contract Best Practices (Consensys/smart-contract-best-practices).
  - Awesome Blockchain Security (the Trail-of-Bits-era curated list, maintained
    at chainflag/awesome-blockchain-security) — audit tooling + references.
  - SWC Registry (full entries) — completes SWC coverage inside
    ``coding_knowledge`` so code-related weakness queries hit the same
    collection (the registry also lives in ``threat_intel`` / ``contract_security``).
  - DeFi hack analysis guides (curated): reentrancy, oracle manipulation,
    flash loans, governance exploits.

Every point is tagged with coding metadata (ecosystem=general,
knowledge_type=checklist|swc_entry|hack_analysis, topic, source_url) and
embedded with 2560-dim qwen3-embedding-4b. Deterministic point IDs → idempotent re-runs.

Usage:
    python scripts/ingest_security_guides.py --dry-run
    python scripts/ingest_security_guides.py --no-fetch  # curated only
    python scripts/ingest_security_guides.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - Infinity at INFINITY_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_security_guides")

sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from coding_ingest_common import (  # noqa: E402
    build_coding_doc,
    collect_markdown,
    fetch_text,
    first_lines,
    ingest_coding_docs,
    markdown_sections,
    read_text,
    sparse_clone,
)
from security_guides_data import ALL_SECURITY_GUIDE_DOCS  # noqa: E402

REPO_DIR = Path(os.getenv("RAG_CODEBASE_DIR", "/tmp/rag_codebases"))  # noqa: S108  # nosec B108

# Solcurity + Consensys best practices repos.
REPOS: list[dict[str, Any]] = [
    {
        "id": "rari-capital-solcurity",
        "repo": "https://github.com/Rari-Capital/solcurity.git",
        "ref": "main",
        "include": ["."],
        "knowledge_type": "checklist",
        "topic": "audit-checklist",
        "limit": 40,
    },
    {
        "id": "consensys-smart-contract-best-practices",
        "repo": "https://github.com/Consensys/smart-contract-best-practices.git",
        "ref": "main",
        "include": ["docs"],
        "knowledge_type": "best_practice",
        "topic": "security",
        "limit": 120,
    },
]

# Live-fetched reference lists (no repo clone).
REMOTE_REFERENCE_DOCS: list[dict[str, Any]] = [
    {
        "source": "awesome-blockchain-security",
        "rel_key": "awesome-blockchain-security",
        "url": "https://raw.githubusercontent.com/chainflag/awesome-blockchain-security/master/README.md",
        "title": "Awesome Blockchain Security (curated audit reference list)",
        "topic": "audit-checklist",
        "knowledge_type": "reference_list",
    },
]

SWC_ENTRIES_URL = (
    "https://api.github.com/repos/SmartContractSecurity/SWC-registry/contents/entries/docs"
)
SWC_RAW_BASE = (
    "https://raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/entries/docs"
)


def _curated_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in ALL_SECURITY_GUIDE_DOCS:
        docs.append(
            build_coding_doc(
                source=entry["source"],
                rel_key=entry["rel_key"],
                content=entry["content"],
                ecosystem="general",
                language="solidity",
                knowledge_type=entry.get("knowledge_type", "best_practice"),
                topic=entry.get("topic", "security"),
                title=entry.get("title", ""),
                url="https://github.com/Rari-Capital/solcurity"
                if entry["source"] == "solcurity"
                else "https://consensys.github.io/smart-contract-best-practices/",
            )
        )
    return docs


async def _fetch_reference_docs(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for spec in REMOTE_REFERENCE_DOCS:
        raw = await fetch_text(client, spec["url"], request_timeout=45.0)
        if not raw:
            logger.warning("reference_fetch_miss source=%s", spec["source"])
            continue
        sections = markdown_sections(raw, max_len=2500)
        for i, sec in enumerate(sections):
            content = f"{spec['title']} — {sec['heading']}\n{sec['text']}"
            docs.append(
                build_coding_doc(
                    source=spec["source"],
                    rel_key=f"{spec['rel_key']}/{i}",
                    content=content,
                    ecosystem="general",
                    language="markdown",
                    knowledge_type=spec["knowledge_type"],
                    topic=spec["topic"],
                    title=first_lines(sec["text"], 2),
                    url=spec["url"],
                )
            )
    logger.info("reference_docs_fetched=%d", len(docs))
    return docs


async def _fetch_swc_entries(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch the full SWC Registry entries into coding_knowledge (completes coverage)."""
    try:
        r = await client.get(
            SWC_ENTRIES_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=45.0,
        )
        if r.status_code != 200:
            logger.warning("swc_list_fail status=%d", r.status_code)
            return []
        entries = r.json()
    except Exception as e:
        logger.warning("swc_list_error err=%s", str(e)[:120])
        return []

    docs: list[dict[str, Any]] = []
    for entry in entries:
        name = entry.get("name", "")
        if not name.endswith(".md"):
            continue
        swc_id = name[:-3]
        raw = await fetch_text(client, f"{SWC_RAW_BASE}/{name}", request_timeout=30.0)
        if not raw:
            continue
        content = f"SWC Registry entry {swc_id}:\n{raw}"
        docs.append(
            build_coding_doc(
                source="swc-registry",
                rel_key=f"swc/{swc_id}",
                content=content,
                ecosystem="ethereum",
                language="solidity",
                knowledge_type="swc_entry",
                topic="swc",
                title=f"SWC {swc_id} — Smart Contract Weakness Classification",
                url=f"https://swcregistry.io/docs/{swc_id}",
                severity="medium",
                chain="ethereum",
                extra_metadata={"swc": swc_id},
            )
        )
    logger.info("swc_entries_fetched=%d", len(docs))
    return docs


def _repo_docs(repo_root: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    src = spec["id"]
    readme = repo_root / "README.md"
    if readme.exists():
        text = read_text(readme)
        if text.strip():
            docs.append(
                build_coding_doc(
                    source=src,
                    rel_key=f"repo/{src}/README",
                    content=text[:60000],
                    ecosystem="general",
                    language="markdown",
                    knowledge_type=spec["knowledge_type"],
                    topic=spec["topic"],
                    title=f"{src} README",
                    url=f"https://github.com/{src.replace('-', '/')}",
                )
            )
    include = spec["include"] if spec["include"] != ["."] else ["."]
    for path in collect_markdown(repo_root, include, limit=spec.get("limit", 100)):
        text = read_text(path)
        if not text.strip():
            continue
        rel = path.relative_to(repo_root).as_posix()
        sections = markdown_sections(text)
        for i, sec in enumerate(sections):
            content = f"{src} — {sec['heading']}\n{sec['text']}"
            docs.append(
                build_coding_doc(
                    source=src,
                    rel_key=f"repo/{src}/{rel}/{i}",
                    content=content,
                    ecosystem="general",
                    language="markdown",
                    knowledge_type=spec["knowledge_type"],
                    topic=spec["topic"],
                    title=first_lines(sec["text"], 2),
                    url=f"https://github.com/{src.replace('-', '/')}/blob/master/{rel}",
                )
            )
    return docs


def _clone_repos() -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    for spec in REPOS:
        target = REPO_DIR / f"ck-{spec['id']}"
        try:
            sparse_clone(spec["repo"], spec["ref"], spec["include"], target)
            roots.append(spec | {"root": target})
        except Exception as e:
            logger.warning("clone_failed id=%s err=%s", spec["id"], str(e)[:200])
    return roots


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    docs: list[dict[str, Any]] = _curated_docs()
    if not args.no_fetch:
        async with httpx.AsyncClient(timeout=60.0) as client:
            docs += await _fetch_reference_docs(client)
            docs += await _fetch_swc_entries(client)
        for spec in _clone_repos():
            docs += _repo_docs(spec["root"], spec)
    if args.source:
        docs = [d for d in docs if args.source in d["rel_key"]]

    logger.info("total_docs_built=%d (curated=%d)", len(docs), len(_curated_docs()))

    async with httpx.AsyncClient(timeout=120.0) as client:
        store = QdrantVectorStore()
        try:
            stats = await ingest_coding_docs(
                client,
                store,
                docs,
                source="security-guides",
                dry_run=args.dry_run,
            )
        finally:
            await store.close()
    return {"docs": len(docs), **stats}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest security-audit guides into coding_knowledge"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--no-fetch", action="store_true", help="Curated content only (no network)")
    parser.add_argument(
        "--source", default=None, help="Only ingest docs whose rel_key contains this"
    )
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print("SECURITY GUIDES INGEST COMPLETE")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
