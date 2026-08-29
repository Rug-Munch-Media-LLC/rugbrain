#!/usr/bin/env python3
"""Ingest security-focused GitHub knowledge repos into the RugBrain RAG.

Two complementary sources (both cloned shallow, idempotent re-runs):

1. crytic/building-secure-contracts — the Crytic (Trail of Bits) "Building
   Secure Contracts" book. Covers EVM + Solana + Algorand vulnerability
   classes, exploit scenarios, mitigations, EVM internals (opcodes, forks,
   yellow paper) and program analysis (Manticore, Slither). Each markdown
   page is chunked, embedded (2560-dim qwen3-embedding-4b via ) and upserted
   into ``contract_security`` (EVM/Solana security patterns) and
   ``coding_knowledge`` (EVM internals + analysis tooling) with canonical
   metadata (ecosystem, vulnerability class, source path).

2. immunefi-team/Web3-Security-Library — Immunefi's curated library of
   real-world hack analyses and vulnerability write-ups. Each analysis is
   embedded into ``defi_exploits`` (hack examples) with the vulnerability
   type and affected protocol in metadata; the vulnerability theory pages
   land in ``crypto_vulnerabilities``.

Point IDs are deterministic UUIDv5 derived from content hashes so re-runs
overwrite the same points instead of duplicating.

Usage:
    python scripts/ingest_security_repos.py --dry-run
    python scripts/ingest_security_repos.py --source building-secure-contracts
    python scripts/ingest_security_repos.py --source web3-security-library
    python scripts/ingest_security_repos.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://127.0.0.1:8004/v1) with qwen3-embedding-4b
  - git (for cloning; repos already cloned are reused)
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
logger = logging.getLogger("ingest_security_repos")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from ingest_common import chunk_text, content_hash, embed_many  # noqa: E402

REPO_BASE = Path(os.getenv("RAG_KNOWLEDGE_DIR", "/tmp/rag-knowledge"))  # noqa: S108  # nosec B108
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 100
MAX_FILES = 400  # safety cap per source

# ── Source definitions ────────────────────────────────────────────────


def _clone(repo_url: str, repo_id: str) -> Path:
    """Shallow-clone *repo_url* into ``REPO_BASE/<repo_id>`` (idempotent)."""
    target = REPO_BASE / repo_id
    if (target / ".git").exists():
        logger.info("repo_present id=%s dir=%s", repo_id, target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"sec-{repo_id}-"))
    try:
        logger.info("cloning id=%s url=%s", repo_id, repo_url)
        r = subprocess.run(  # noqa: S603 — url is a fixed constant
            ["git", "clone", "--depth", "1", repo_url, str(tmp)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"clone failed for {repo_id}: {r.stderr[-300:]}")
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(tmp), str(target))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return target


def _md_files(
    root: Path, include_subdirs: tuple[str, ...] | None = None, limit: int = MAX_FILES
) -> list[Path]:
    """Return sorted markdown files under *root* (optionally only subdirs)."""
    files: list[Path] = []
    for inc in include_subdirs or ("",):
        base = root / inc if inc else root
        if not base.exists():
            logger.warning("include_missing path=%s", base)
            continue
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            if "/node_modules/" in rel or "/.git/" in rel:
                continue
            files.append(p)
            if len(files) >= limit:
                return files
    return files[:limit]


def _strip_md(text: str) -> str:
    """Light markdown cleanup: drop fenced code blocks markers but keep code."""
    text = re.sub(r"```\w*\s*", "```\n", text)
    return text


_VULN_CLASS_RE = re.compile(
    r"(reentranc|oracle|flash ?loan|access ?control|signature|overflow|underflow|"
    r"arithmetic|governance|bridge|phish|drain|tx\.origin|delegatecall|selfdestruct|"
    r"arbitrary ?cpi|signer|account ?check|rekey|closing|asset|fee|mint|burn|"
    r"initialize|upgrade|proxy)"
)


def _detect_class(path: str, content: str) -> str:
    """Best-effort vulnerability class from path + content keywords."""
    lowered = f"{path} {content[:4000]}".lower()
    m = _VULN_CLASS_RE.search(lowered)
    return m.group(1).replace(" ", "-") if m else "unknown"


def _detect_ecosystem(path: str) -> str:
    lowered = path.lower()
    if "solana" in lowered:
        return "solana"
    if "algorand" in lowered:
        return "algorand"
    return "evm"


# ── building-secure-contracts ─────────────────────────────────────────


def _bsc_docs(repo_root: Path) -> list[dict[str, Any]]:
    """Build ingest docs from crytic/building-secure-contracts."""
    docs: list[dict[str, Any]] = []

    # Vulnerability classes with exploit scenarios + mitigations.
    for sub in ("not-so-smart-contracts",):
        for f in _md_files(repo_root, (sub,), limit=MAX_FILES):
            rel = f.relative_to(repo_root).as_posix()
            try:
                text = _strip_md(f.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            if len(text.strip()) < 80:
                continue
            ecosystem = _detect_ecosystem(rel)
            vuln_class = _detect_class(rel, text)
            doc_id = f"sec:bsc:{rel}:{content_hash(text)}"
            docs.append(
                {
                    "doc_id": doc_id,
                    "text": text,
                    "collection": "contract_security",
                    "source": "building-secure-contracts",
                    "severity": "high",
                    "chain": ecosystem,
                    "metadata": {
                        "source_type": "security_repo",
                        "vulnerability_class": vuln_class,
                        "ecosystem": ecosystem,
                        "path": rel,
                        "repo": "crytic/building-secure-contracts",
                        "verdict": "bad_example" if "not-so-smart-contracts" in rel else "",
                    },
                }
            )

    # EVM internals + program analysis tooling → coding_knowledge.
    for sub in ("learn_evm", "program-analysis"):
        for f in _md_files(repo_root, (sub,), limit=MAX_FILES):
            rel = f.relative_to(repo_root).as_posix()
            try:
                text = _strip_md(f.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            if len(text.strip()) < 120:
                continue
            doc_id = f"sec:bsc:{rel}:{content_hash(text)}"
            topic = "evm-internals" if "learn_evm" in rel else "program-analysis"
            docs.append(
                {
                    "doc_id": doc_id,
                    "text": text,
                    "collection": "coding_knowledge",
                    "source": "building-secure-contracts",
                    "severity": "medium",
                    "chain": "evm",
                    "metadata": {
                        "source_type": "security_repo",
                        "topic": topic,
                        "path": rel,
                        "repo": "crytic/building-secure-contracts",
                    },
                }
            )
    return docs


# ── Web3-Security-Library ─────────────────────────────────────────────


def _wsl_docs(repo_root: Path) -> list[dict[str, Any]]:
    """Build ingest docs from immunefi-team/Web3-Security-Library."""
    docs: list[dict[str, Any]] = []
    # Hack analyses → defi_exploits.
    for f in _md_files(repo_root, ("HackAnalyses", "BugFixReviews"), limit=MAX_FILES):
        rel = f.relative_to(repo_root).as_posix()
        try:
            text = _strip_md(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if len(text.strip()) < 80:
            continue
        vuln_class = _detect_class(rel, text)
        doc_id = f"sec:wsl:{rel}:{content_hash(text)}"
        docs.append(
            {
                "doc_id": doc_id,
                "text": text,
                "collection": "defi_exploits",
                "source": "web3-security-library",
                "severity": "high",
                "chain": "evm",
                "metadata": {
                    "source_type": "security_repo",
                    "vulnerability_class": vuln_class,
                    "path": rel,
                    "repo": "immunefi-team/Web3-Security-Library",
                    "sub_source": "hack-analysis",
                },
            }
        )
    # Vulnerability theory → crypto_vulnerabilities.
    for f in _md_files(repo_root, ("Vulnerabilities",), limit=MAX_FILES):
        rel = f.relative_to(repo_root).as_posix()
        try:
            text = _strip_md(f.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if len(text.strip()) < 80:
            continue
        vuln_class = _detect_class(rel, text)
        doc_id = f"sec:wsl:{rel}:{content_hash(text)}"
        docs.append(
            {
                "doc_id": doc_id,
                "text": text,
                "collection": "crypto_vulnerabilities",
                "source": "web3-security-library",
                "severity": "medium",
                "chain": "evm",
                "metadata": {
                    "source_type": "security_repo",
                    "vulnerability_class": vuln_class,
                    "path": rel,
                    "repo": "immunefi-team/Web3-Security-Library",
                    "sub_source": "vulnerability-theory",
                },
            }
        )
    return docs


# ── Ingest ────────────────────────────────────────────────────────────


async def ingest_docs(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    docs: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, int]:
    """Chunk, embed and upsert *docs* (deterministic IDs → idempotent)."""
    stats = {"files": len(docs), "chunks": 0, "inserted": 0, "failed": 0}
    if dry_run:
        total = sum(max(1, (len(d["text"]) + CHUNK_SIZE - 1) // CHUNK_SIZE) for d in docs)
        logger.info("[DRY RUN] %d files / ~%d chunks", len(docs), total)
        return stats

    for i, doc in enumerate(docs, start=1):
        text = (doc.get("text") or "").strip()
        if not text:
            continue
        windows = chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)
        vectors = await embed_many(client, [w[:8000] for w in windows])
        for ci, (window, vector) in enumerate(zip(windows, vectors, strict=False)):
            if not vector:
                stats["failed"] += 1
                continue
            chunk_id = f"{doc['doc_id']}:{content_hash(window)}:{ci}"
            ok = await store.insert(
                doc_id=chunk_id,
                collection=doc["collection"],
                embedding=vector,
                content=window,
                metadata=doc.get("metadata", {}),
                source=doc["source"],
                severity=doc.get("severity", "medium"),
                chain=doc.get("chain", ""),
            )
            if ok:
                stats["inserted"] += 1
            else:
                stats["failed"] += 1
            stats["chunks"] += 1
        if i % 25 == 0 or i == len(docs):
            logger.info(
                "progress %d/%d files, %d chunks upserted (%d failed)",
                i,
                len(docs),
                stats["inserted"],
                stats["failed"],
            )
    logger.info(
        "done files=%d chunks=%d inserted=%d failed=%d",
        stats["files"],
        stats["chunks"],
        stats["inserted"],
        stats["failed"],
    )
    return stats


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    client = httpx.AsyncClient(timeout=900.0)
    store = QdrantVectorStore()
    all_stats: dict[str, dict[str, int]] = {}
    try:
        if args.source in (None, "building-secure-contracts"):
            repo = _clone(
                "https://github.com/crytic/building-secure-contracts.git",
                "building-secure-contracts",
            )
            docs = _bsc_docs(repo)
            all_stats["building-secure-contracts"] = await ingest_docs(
                client, store, docs, args.dry_run
            )
        if args.source in (None, "web3-security-library"):
            repo = _clone(
                "https://github.com/immunefi-team/Web3-Security-Library.git",
                "Web3-Security-Library",
            )
            docs = _wsl_docs(repo)
            all_stats["web3-security-library"] = await ingest_docs(
                client, store, docs, args.dry_run
            )
    finally:
        await client.aclose()
        await store.close()
    return all_stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest security GitHub knowledge repos into RAG")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument(
        "--source",
        choices=["building-secure-contracts", "web3-security-library"],
        default=None,
        help="Only ingest one source (default: both)",
    )
    args = parser.parse_args()
    stats = asyncio.run(main_async(args))
    for src, s in stats.items():
        print(
            f"{src.upper()} INGEST COMPLETE: {s['inserted']} points / {s['chunks']} chunks ({s['failed']} failed)"
        )


if __name__ == "__main__":
    main()
