#!/usr/bin/env python3
"""Ingest Move / Cairo (alternative VM) coding knowledge into the RugBrain
``coding_knowledge`` collection.

Part 3 of the coding-knowledge expansion. Merges curated reference content
with documentation cloned from:

  - move-language/move — the Move language repo (resources, abilities,
    modules, scripts, Move Prover).
  - starkware-libs/cairo — the Cairo repo (StarkNet contracts, components,
    formal verification).

Every point is tagged with coding metadata (ecosystem=move|cairo,
language=move|cairo, knowledge_type, topic, source_url) and embedded with
2560-dim qwen3-embedding-4b. Deterministic point IDs → idempotent re-runs.

Usage:
    python scripts/ingest_alt_vm_knowledge.py --dry-run
    python scripts/ingest_alt_vm_knowledge.py --no-clone  # curated only
    python scripts/ingest_alt_vm_knowledge.py --max-chunks 400

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at INFINITY_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
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
logger = logging.getLogger("ingest_altvm")

sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from alt_vm_knowledge_data import ALL_ALT_VM_DOCS  # noqa: E402
from coding_ingest_common import (  # noqa: E402
    build_coding_doc,
    collect_markdown,
    first_lines,
    ingest_coding_docs,
    markdown_sections,
    read_text,
    sparse_clone,
)

REPO_DIR = Path(os.getenv("RAG_CODEBASE_DIR", "/tmp/rag_codebases"))  # noqa: S108  # nosec B108

REPOS: list[dict[str, Any]] = [
    {
        "id": "move-language-move",
        "repo": "https://github.com/move-language/move.git",
        "ref": "main",
        "include": ["documentation", "language", "tutorial"],
        "ecosystem": "move",
        "language": "move",
        "knowledge_type": "language_docs",
        "topic": "move",
        "limit": 150,
    },
    {
        "id": "starkware-libs-cairo",
        "repo": "https://github.com/starkware-libs/cairo.git",
        "ref": "v2.6.3",
        "include": ["docs"],
        "ecosystem": "cairo",
        "language": "cairo",
        "knowledge_type": "language_docs",
        "topic": "cairo",
        "limit": 150,
    },
]


def _curated_docs() -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for entry in ALL_ALT_VM_DOCS:
        ecosystem = "move" if entry["source"] == "move-docs" else "cairo"
        language = "move" if ecosystem == "move" else "cairo"
        docs.append(
            build_coding_doc(
                source=entry["source"],
                rel_key=entry["rel_key"],
                content=entry["content"],
                ecosystem=ecosystem,
                language=language,
                knowledge_type=entry.get("knowledge_type", "language_docs"),
                topic=entry.get("topic", ""),
                title=entry.get("title", ""),
                url=(
                    "https://github.com/move-language/move/tree/main/documentation"
                    if ecosystem == "move"
                    else "https://book.cairo-lang.org"
                ),
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
                    ecosystem=spec["ecosystem"],
                    language=spec["language"],
                    knowledge_type=spec["knowledge_type"],
                    topic=spec["topic"],
                    title=f"{src} README",
                    url=f"https://github.com/{src.replace('-', '/')}",
                )
            )
    for path in collect_markdown(repo_root, spec["include"], limit=spec.get("limit", 150)):
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
                    ecosystem=spec["ecosystem"],
                    language=spec["language"],
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
                source="altvm",
                dry_run=args.dry_run,
            )
        finally:
            await store.close()
    return {"docs": len(docs), **stats}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Move/Cairo coding knowledge into coding_knowledge"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--no-clone", action="store_true", help="Curated content only (no clones)")
    parser.add_argument(
        "--source", default=None, help="Only ingest docs whose rel_key contains this"
    )
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print("MOVE/CAIRO KNOWLEDGE INGEST COMPLETE")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
