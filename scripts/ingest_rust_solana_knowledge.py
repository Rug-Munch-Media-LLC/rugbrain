#!/usr/bin/env python3
"""Ingest Rust / Solana coding knowledge into the RugBrain ``coding_knowledge`` collection.

Part 2 of the coding-knowledge expansion. Merges curated reference content
with documentation cloned from:

  - solana-labs/solana — program docs + SDK docs (accounts, PDAs, CPIs,
    token program, associated token accounts, rent, system program, BPF).
  - coral-xyz/anchor — the Anchor framework docs (account constraints, PDAs,
    CPIs, errors) + example programs.
  - neodyme-labs/solana-security-txt — Solana security best practices /
    SECURITY.txt convention.

Every point is tagged with coding metadata (ecosystem=solana,
language=rust, knowledge_type, topic, source_url) and embedded with 1024-dim
bge-m3. Deterministic point IDs → idempotent re-runs.

Usage:
    python scripts/ingest_rust_solana_knowledge.py --dry-run
    python scripts/ingest_rust_solana_knowledge.py --no-clone  # curated only
    python scripts/ingest_rust_solana_knowledge.py --max-chunks 500

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
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
logger = logging.getLogger("ingest_solana")

sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from coding_ingest_common import (  # noqa: E402
    build_coding_doc,
    collect_markdown,
    first_lines,
    ingest_coding_docs,
    markdown_sections,
    read_text,
    sparse_clone,
)
from rust_solana_knowledge_data import ALL_RUST_SOLANA_DOCS  # noqa: E402

REPO_DIR = Path(os.getenv("RAG_CODEBASE_DIR", "/tmp/rag_codebases"))  # noqa: S108  # nosec B108

REPOS: list[dict[str, Any]] = [
    {
        "id": "solana-labs-solana",
        "repo": "https://github.com/solana-labs/solana.git",
        "ref": "v1.18.18",
        "include": ["docs", "sdk"],
        "knowledge_type": "language_docs",
        "topic": "solana",
        "limit": 150,
    },
    {
        "id": "coral-xyz-anchor",
        "repo": "https://github.com/coral-xyz/anchor.git",
        "ref": "v0.30.1",
        "include": ["docs", "examples"],
        "knowledge_type": "framework_docs",
        "topic": "anchor",
        "limit": 200,
    },
    {
        "id": "solana-security-txt",
        "repo": "https://github.com/neodyme-labs/solana-security-txt.git",
        "ref": "master",
        "include": ["."],
        "knowledge_type": "security_pattern",
        "topic": "security",
        "limit": 60,
    },
]


def _curated_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in ALL_RUST_SOLANA_DOCS:
        docs.append(
            build_coding_doc(
                source=entry["source"],
                rel_key=entry["rel_key"],
                content=entry["content"],
                ecosystem="solana",
                language="rust",
                knowledge_type=entry.get("knowledge_type", "language_docs"),
                topic=entry.get("topic", ""),
                title=entry.get("title", ""),
                url="https://docs.solana.com" if entry["source"] == "solana-docs" else "",
            )
        )
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
                    ecosystem="solana",
                    language="rust",
                    knowledge_type=spec["knowledge_type"],
                    topic=spec["topic"],
                    title=f"{src} README",
                    url=f"https://github.com/{src.replace('-', '/')}",
                )
            )
    include = spec["include"] if spec["include"] != ["."] else ["."]
    for path in collect_markdown(repo_root, include, limit=spec.get("limit", 200)):
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
                    ecosystem="solana",
                    language="rust",
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
    if not args.no_clone:
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
                source="solana",
                dry_run=args.dry_run,
            )
        finally:
            await store.close()
    return {"docs": len(docs), **stats}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Rust/Solana coding knowledge into coding_knowledge"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--no-clone", action="store_true", help="Curated content only (no clones)")
    parser.add_argument(
        "--source", default=None, help="Only ingest docs whose rel_key contains this"
    )
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print("RUST/SOLANA KNOWLEDGE INGEST COMPLETE")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
