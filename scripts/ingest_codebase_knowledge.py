#!/usr/bin/env python3
"""Ingest known-good smart-contract codebases into the RugBrain knowledge graph.

Part 1 of the contract-knowledge expansion. Clones public reference
codebases and ingests their source (split by contract/function, embedded with
qwen3-embedding-4b via ) into the RAG so RugBrain can answer "what does a secure
ERC20 / Uniswap pool / SPL program look like?".

Protocols (see ``codebase_protocols_data.py``):
    OpenZeppelin, Solana Program Library, Uniswap v2/v3/v4, Aave v3,
    Compound, Chainlink, EigenLayer.

Collections:
    solidity_patterns — EVM Solidity contract chunks (verdict=good)
    solana_patterns   — Solana Rust program chunks (verdict=good)

For each file we:
  1. Extract Solidity/Rust source.
  2. Split by logical unit (contract/function via the AST chunker for
     Solidity; ``pub fn``/``pub struct`` boundaries for Rust).
  3. Embed each chunk (2560-dim qwen3-embedding-4b, batched).
  4. Upsert into the target collection with protocol/version/vulnerability
     history metadata.

Idempotent: point IDs are deterministic UUIDv5 derived from content hashes,
so re-running overwrites the same points instead of duplicating.

Usage:
    python scripts/ingest_codebase_knowledge.py --dry-run
    python scripts/ingest_codebase_knowledge.py --protocol openzeppelin
    python scripts/ingest_codebase_knowledge.py --max-chunks 4000

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at INFINITY_URL (default http://172.22.0.1:8004/v1) with qwen3-embedding-4b
  - git, solc (optional — falls back to regex chunking)
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
from rugbrain.solidity_chunker import chunk_solidity_ast

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_codebase")

# scripts/ dir on sys.path so sibling data modules import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from codebase_protocols_data import PROTOCOLS  # noqa: E402
from ingest_common import content_hash, embed_many  # noqa: E402

REPO_DIR = os.getenv("RAG_CODEBASE_DIR", "/tmp/rag_codebases")  # noqa: S108  # nosec B108

DEFAULT_MAX_CONTRACTS = 80  # contracts/programs per protocol
DEFAULT_MAX_CHUNKS = 6000  # global chunk cap (embedding is cheap but bounded)

_CONTRACT_NAME_RE = re.compile(r"\b(?:contract|library|interface)\s+(\w+)")
_RUST_SPLIT_RE = re.compile(
    r"(?=pub\s+(?:async\s+)?fn\s+\w+|pub\s+struct\s+\w+|pub\s+enum\s+\w+|pub\s+mod\s+\w+)"
)

SKIP_DIRS = {"node_modules", "target", ".git"}
SKIP_FILE_PARTS = (".t.sol", ".spec.ts", ".test.ts", "Mock", "mock")


# ── Repo handling ────────────────────────────────────────────────────


def _clone_repo(protocol: dict[str, Any], base_dir: Path) -> Path:
    """Shallow-clone *protocol* into ``base_dir/<id>`` (idempotent).

    If the configured tag no longer exists upstream (tags drift), falls back
    to the default branch with a warning instead of aborting the ingest.
    """
    target: Path = Path(str(base_dir / protocol["id"]))
    if (target / ".git").exists():
        logger.info("repo_present id=%s dir=%s", protocol["id"], target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tag = protocol.get("tag")
    logger.info("cloning id=%s tag=%s → %s", protocol["id"], tag or "default", target)

    def _clone(branch: str | None) -> subprocess.CompletedProcess[str]:
        cmd = ["git", "clone", "--depth", "1"]
        if branch:
            cmd += ["--branch", branch]
        cmd += ["--single-branch", protocol["repo"], str(tmp)]
        return subprocess.run(  # noqa: S603 — repo/tag are fixed constants
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )

    tmp = Path(tempfile.mkdtemp(prefix=f"kb-{protocol['id']}-"))
    try:
        result = _clone(tag)
        if result.returncode != 0 and tag:
            # Tag drifted upstream (e.g. removed) — retry on the default branch.
            logger.warning(
                "clone_tag_missing id=%s tag=%s err=%s — falling back to default branch",
                protocol["id"],
                tag,
                result.stderr[-200:],
            )
            result = _clone(None)
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed for {protocol['id']}: {result.stderr[-300:]}")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(tmp), str(target))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    size_mb = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) / 1e6
    logger.info("clone_complete id=%s size_mb=%.1f", protocol["id"], size_mb)
    _target: Path = target

    return _target


def _is_skipped(rel_path: str) -> bool:
    """True when the path is a test/mock/generated file we should skip."""
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts):
        return True
    if any(
        p.lower() in ("test", "tests", "mock", "mocks", "examples", "bench", "benches")
        for p in parts
    ):
        return True
    lowered = rel_path.lower()
    return any(part in lowered for part in SKIP_FILE_PARTS)


def _collect_files(repo_root: Path, protocol: dict[str, Any], limit: int) -> list[Path]:
    """Return the protocol's source files, capped at *limit* (deterministic)."""
    ext = ".sol" if protocol["language"] == "solidity" else ".rs"
    includes = tuple(protocol.get("include") or (""))
    files: list[Path] = []
    for inc in includes:
        base = repo_root / inc
        if not base.exists():
            logger.warning("include_missing id=%s path=%s", protocol["id"], inc)
            continue
        for p in sorted(base.rglob(f"*{ext}")):
            rel = p.relative_to(repo_root).as_posix()
            if _is_skipped(rel):
                continue
            files.append(p)
            if len(files) >= limit:
                return files
    return files[:limit]


# ── Splitting ────────────────────────────────────────────────────────


def split_solidity(code: str) -> list[str]:
    """Split Solidity source into logical units (AST-aware, regex fallback)."""
    chunks = chunk_solidity_ast(code)
    return [c for c in chunks if c.strip()]


def split_rust(code: str) -> list[str]:
    """Split Rust program source at ``pub fn/struct/enum/mod`` boundaries."""
    if len(code) <= 3500:
        return [code] if code.strip() else []
    parts = _RUST_SPLIT_RE.split(code)
    chunks: list[str] = []
    current = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(current) + len(part) > 3500 and current:
            chunks.append(current)
            current = part
        else:
            current += ("\n\n" if current else "") + part
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) > 120]


def _contract_names(chunk: str, language: str) -> list[str]:
    """Extract contract/program names referenced by a chunk."""
    if language == "rust":
        names = re.findall(r"pub\s+(?:fn|struct|enum)\s+(\w+)", chunk)
        return list(dict.fromkeys(names))[:4]
    return list(dict.fromkeys(_CONTRACT_NAME_RE.findall(chunk)))[:4]


# ── Ingest ───────────────────────────────────────────────────────────


async def ingest_protocol(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    protocol: dict[str, Any],
    repo_root: Path,
    max_contracts: int,
    chunk_budget: list[int],
    dry_run: bool,
) -> dict[str, Any]:
    """Ingest one protocol's source into its target collection."""
    collection = protocol["collection"]
    language = protocol["language"]
    files = _collect_files(repo_root, protocol, max_contracts)
    stats = {"id": protocol["id"], "files": len(files), "chunks": 0, "inserted": 0, "skipped": 0}

    if dry_run:
        logger.info(
            "[DRY] id=%s files=%d (would chunk+embed into %s)",
            protocol["id"],
            len(files),
            collection,
        )
        return stats

    for file in files:
        if chunk_budget[0] <= 0:
            break
        try:
            code = file.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.warning("read_failed id=%s path=%s err=%s", protocol["id"], file, e)
            continue
        if not code.strip():
            continue

        chunks = split_solidity(code) if language == "solidity" else split_rust(code)
        rel_path = file.relative_to(repo_root).as_posix()

        batch: list[str] = []
        batch_ids: list[str] = []
        batch_meta: list[dict[str, Any]] = []
        for ci, chunk in enumerate(chunks):
            if chunk_budget[0] <= 0:
                break
            names = _contract_names(chunk, language)
            doc_id = f"kb:{protocol['id']}:{rel_path}:{ci}:{content_hash(chunk)}"
            metadata = {
                "protocol": protocol["name"],
                "protocol_id": protocol["id"],
                "version": protocol.get("tag", ""),
                "language": language,
                "verdict": protocol.get("verdict", "good"),
                "knowledge_type": "codebase",
                "source_url": f"{protocol['repo'].removesuffix('.git')}/blob/main/{rel_path}",
                "rel_path": rel_path,
                "contract_names": names,
                "chunk_index": ci,
                "vulnerability_history": protocol.get("vulnerability_history", "")[:2000],
            }
            batch.append(chunk)
            batch_ids.append(doc_id)
            batch_meta.append(metadata)
            chunk_budget[0] -= 1

        vectors = await embed_many(client, batch)
        for doc_id, chunk, metadata, vector in zip(
            batch_ids, batch, batch_meta, vectors, strict=False
        ):
            if not vector:
                stats["skipped"] += 1
                continue
            ok = await store.insert(
                doc_id=doc_id,
                collection=collection,
                embedding=vector,
                content=chunk,
                metadata=metadata,
                source=f"codebase:{protocol['id']}",
                severity="info",
                chain="ethereum" if language == "solidity" else "solana",
            )
            if ok:
                stats["inserted"] += 1
                stats["chunks"] += 1
            else:
                stats["skipped"] += 1
        logger.info(
            "progress id=%s files_done=%d inserted=%d budget_left=%d",
            protocol["id"],
            stats["files"] - len(files),
            stats["inserted"],
            chunk_budget[0],
        )
    return stats


async def main_async(args: argparse.Namespace) -> list[dict[str, Any]]:
    base_dir = Path(REPO_DIR)
    protocols = [p for p in PROTOCOLS if not args.protocol or p["id"] == args.protocol]
    if not protocols:
        logger.error(
            "unknown protocol=%s (choose from %s)", args.protocol, [p["id"] for p in PROTOCOLS]
        )
        return []

    chunk_budget = [args.max_chunks]
    all_stats: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=120.0) as client:
        store = QdrantVectorStore()
        try:
            for protocol in protocols:
                repo_root = (
                    _clone_repo(protocol, base_dir)
                    if not args.no_clone
                    else base_dir / protocol["id"]
                )
                if not repo_root.exists():
                    logger.error("repo_missing id=%s dir=%s", protocol["id"], repo_root)
                    continue
                stats = await ingest_protocol(
                    client,
                    store,
                    protocol,
                    repo_root,
                    args.max_contracts,
                    chunk_budget,
                    args.dry_run,
                )
                all_stats.append(stats)
        finally:
            await store.close()
    return all_stats


def main() -> None:
    global REPO_DIR  # must be declared before any use of REPO_DIR below

    parser = argparse.ArgumentParser(
        description="Ingest known-good contract codebases into solidity_patterns / solana_patterns"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview counts only (no clone/embed)"
    )
    parser.add_argument("--protocol", default=None, help="Only ingest this protocol id")
    parser.add_argument(
        "--max-contracts", type=int, default=DEFAULT_MAX_CONTRACTS, help="Max files per protocol"
    )
    parser.add_argument(
        "--max-chunks", type=int, default=DEFAULT_MAX_CHUNKS, help="Global chunk cap"
    )
    parser.add_argument("--no-clone", action="store_true", help="Use existing repos (skip cloning)")
    parser.add_argument("--repo-dir", default=REPO_DIR, help="Directory to clone repos into")
    args = parser.parse_args()

    REPO_DIR = args.repo_dir

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print("CODEBASE KNOWLEDGE INGEST COMPLETE")
    for s in stats:
        print(
            f"  {s['id']:<12} files={s['files']:<4} chunks={s['chunks']:<5} "
            f"inserted={s['inserted']} skipped={s['skipped']}"
        )
    print("=" * 60)


if __name__ == "__main__":
    main()
