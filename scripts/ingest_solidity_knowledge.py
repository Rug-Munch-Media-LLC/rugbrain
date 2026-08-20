#!/usr/bin/env python3
"""Ingest Solidity / EVM coding knowledge into the RugBrain ``coding_knowledge`` collection.

Part 1 of the coding-knowledge expansion. Merges curated reference content
with live-fetched official documentation:

  - Official Solidity docs (docs.soliditylang.org ``_sources`` .rst) — syntax,
    control structures, contracts, inheritance, libraries, interfaces, events,
    modifiers, error handling, assembly/Yul, ABI encoding, storage layout,
    security considerations and common patterns.
  - ethereum/solidity repo (README + docs directory).
  - OpenZeppelin Contracts docs (docs.openzeppelin.com) — ReentrancyGuard,
    Ownable, AccessControl, ERC20/ERC721, proxies.
  - Solidity Patterns (fravoll.github.io/solidity-patterns/) — CEI, withdrawal,
    emergency stop, access restriction, randomness.
  - Solidity by Example (solidity-by-example.org source repo) — runnable
    code examples with explanations.

Every point is tagged with coding metadata (ecosystem=ethereum,
language=solidity, knowledge_type, topic, source_url) and embedded with
2560-dim qwen3-embedding-4b. Deterministic point IDs → idempotent re-runs.

Usage:
    python scripts/ingest_solidity_knowledge.py --dry-run
    python scripts/ingest_solidity_knowledge.py --no-fetch   # curated only
    python scripts/ingest_solidity_knowledge.py --max-chunks 500

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
logger = logging.getLogger("ingest_solidity")

# scripts/ dir on sys.path so sibling modules import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from coding_ingest_common import (  # noqa: E402
    build_coding_doc,
    collect_markdown,
    fetch_text,
    first_lines,
    html_to_text,
    ingest_coding_docs,
    markdown_sections,
    read_text,
    rst_to_text,
    sparse_clone,
)
from solidity_knowledge_data import ALL_SOLIDITY_DOCS  # noqa: E402

DOCS_BASE = "https://docs.soliditylang.org/en/latest/_sources"
OZ_BASE = "https://docs.openzeppelin.com/contracts/5.x/api"
REPO_DIR = Path(os.getenv("RAG_CODEBASE_DIR", "/tmp/rag_codebases"))  # noqa: S108  # nosec B108

# (rst page, topic, title) — official Solidity docs pages.
SOLIDITY_DOC_PAGES: list[tuple[str, str, str]] = [
    ("introduction-to-smart-contracts", "introduction", "Introduction to smart contracts"),
    ("layout-of-source-files", "syntax", "Layout of a Solidity source file"),
    ("structure-of-a-contract", "contracts", "Structure of a contract"),
    ("types", "types", "Solidity types"),
    ("units-and-global-variables", "transactions", "Units and global variables"),
    ("control-structures", "control-structures", "Control structures"),
    (
        "contracts",
        "contracts",
        "Contracts — inheritance, interfaces, libraries, events, modifiers, errors",
    ),
    (
        "common-patterns",
        "patterns",
        "Common patterns — withdrawal, access restriction, state machine",
    ),
    ("assembly", "assembly", "Inline assembly"),
    ("yul", "assembly", "Yul — intermediate language"),
    ("abi-spec", "abi-encoding", "Contract ABI specification"),
    ("internals/layout_in_storage", "storage-layout", "Layout of state variables in storage"),
    ("security-considerations", "security", "Security considerations"),
    ("solidity-by-example", "examples", "Solidity by example (official)"),
    ("cheatsheet", "reference", "Cheatsheet"),
]

# (api page, topic, title) — OpenZeppelin docs.
OZ_PAGES: list[tuple[str, str, str]] = [
    ("utils", "reentrancy", "OpenZeppelin utilities — ReentrancyGuard, Pausable, nonces"),
    ("access", "access-control", "OpenZeppelin access control — Ownable, AccessControl"),
    ("token/ERC20", "erc20", "OpenZeppelin ERC20 token API"),
    ("token/ERC721", "erc721", "OpenZeppelin ERC721 token API"),
    ("proxy", "proxies", "OpenZeppelin proxy contracts — UUPS, transparent, ERC1967"),
    ("security", "security", "OpenZeppelin security utilities"),
]

# (repo, ref, sparse paths, source-id, knowledge_type)
REPOS: list[dict[str, Any]] = [
    {
        "id": "ethereum-solidity",
        "repo": "https://github.com/ethereum/solidity.git",
        "ref": "v0.8.24",
        "include": ["docs"],
        "knowledge_type": "language_docs",
        "topic": "docs",
    },
    {
        "id": "solidity-by-example",
        "repo": "https://github.com/solidity-by-example/solidity-by-example.github.io.git",
        "ref": "master",
        "include": ["src/pages"],
        "knowledge_type": "code_example",
        "topic": "examples",
    },
    {
        "id": "solidity-patterns",
        "repo": "https://github.com/fravoll/solidity-patterns.git",
        "ref": "master",
        "include": ["docs"],
        "knowledge_type": "security_pattern",
        "topic": "patterns",
    },
]


def _curated_docs() -> list[dict[str, Any]]:
    """Build docs from the curated Solidity knowledge module."""
    docs: list[dict[str, Any]] = []
    for entry in ALL_SOLIDITY_DOCS:
        docs.append(
            build_coding_doc(
                source=entry["source"],
                rel_key=entry["rel_key"],
                content=entry["content"],
                ecosystem="ethereum",
                language="solidity",
                knowledge_type=entry.get("knowledge_type", "language_docs"),
                topic=entry.get("topic", ""),
                title=entry.get("title", ""),
                url=(
                    f"https://docs.soliditylang.org/{entry['rel_key']}"
                    if entry["source"] == "solidity-docs"
                    else ""
                ),
            )
        )
    return docs


async def _fetch_solidity_docs(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch official Solidity doc pages (rst → text → sections)."""
    docs: list[dict[str, Any]] = []
    for page, topic, title in SOLIDITY_DOC_PAGES:
        raw = await fetch_text(client, f"{DOCS_BASE}/{page}.rst.txt", request_timeout=45.0)
        if not raw:
            logger.warning("doc_fetch_miss page=%s", page)
            continue
        text = rst_to_text(raw)
        sections = markdown_sections(text)
        for i, sec in enumerate(sections):
            content = f"Solidity docs — {title}. {sec['heading']}\n{sec['text']}"
            docs.append(
                build_coding_doc(
                    source="solidity-docs",
                    rel_key=f"docs/{page}/{i}",
                    content=content,
                    ecosystem="ethereum",
                    language="solidity",
                    knowledge_type="language_docs",
                    topic=topic,
                    title=title,
                    url=f"https://docs.soliditylang.org/en/latest/{page}.html",
                )
            )
    logger.info("solidity_docs_fetched pages=%d docs=%d", len(SOLIDITY_DOC_PAGES), len(docs))
    return docs


async def _fetch_openzeppelin_docs(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch OpenZeppelin API docs pages (HTML → text → sections)."""
    docs: list[dict[str, Any]] = []
    for page, topic, title in OZ_PAGES:
        raw = await fetch_text(client, f"{OZ_BASE}/{page}", request_timeout=45.0)
        if not raw:
            logger.warning("oz_fetch_miss page=%s", page)
            continue
        text = html_to_text(raw)
        sections = markdown_sections(text)
        for i, sec in enumerate(sections):
            content = f"OpenZeppelin Contracts docs — {title}. {sec['heading']}\n{sec['text']}"
            docs.append(
                build_coding_doc(
                    source="openzeppelin-docs",
                    rel_key=f"oz/{page}/{i}",
                    content=content,
                    ecosystem="ethereum",
                    language="solidity",
                    knowledge_type="framework_docs",
                    topic=topic,
                    title=title,
                    url=f"{OZ_BASE}/{page}",
                )
            )
    logger.info("openzeppelin_docs_fetched pages=%d docs=%d", len(OZ_PAGES), len(docs))
    return docs


def _repo_docs(repo_root: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Build docs from a cloned repo's markdown files."""
    docs: list[dict[str, Any]] = []
    src = spec["id"]
    # Root README first (small repos carry the main knowledge there).
    readme = repo_root / "README.md"
    if readme.exists():
        text = read_text(readme)
        if text.strip():
            docs.append(
                build_coding_doc(
                    source=src,
                    rel_key=f"repo/{src}/README",
                    content=text[:60000],
                    ecosystem="ethereum",
                    language="solidity",
                    knowledge_type=spec["knowledge_type"],
                    topic=spec["topic"],
                    title=f"{src} README",
                    url=f"https://github.com/{src.replace('-', '/')}",
                )
            )
    for path in collect_markdown(repo_root, spec["include"], limit=300):
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
                    ecosystem="ethereum",
                    language="solidity",
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
            docs += await _fetch_solidity_docs(client)
            docs += await _fetch_openzeppelin_docs(client)
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
                source="solidity",
                dry_run=args.dry_run,
            )
        finally:
            await store.close()
    return {"docs": len(docs), **stats}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Solidity/EVM coding knowledge into coding_knowledge"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--no-fetch", action="store_true", help="Curated content only (no network)")
    parser.add_argument(
        "--source", default=None, help="Only ingest docs whose rel_key contains this"
    )
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print("SOLIDITY KNOWLEDGE INGEST COMPLETE")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
