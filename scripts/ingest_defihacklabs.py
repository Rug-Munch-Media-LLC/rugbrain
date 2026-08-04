#!/usr/bin/env python3
"""Ingest DeFiHackLabs Solidity attack PoCs into the RAG ``threat_intel`` collection.

Source: https://github.com/SunWeb3Sec/DeFiHackLabs (843+ DeFi hack reproduction PoCs)

Each PoC is a Foundry ``ContractTest`` in ``src/test/YYYY-MM/<Protocol>_exp.sol``
that reproduces a real DeFi exploit. For every ``.sol`` file we:

  1. Extract the contract name, attack type (from path + content keywords +
     the incident metadata in ``past/*/README.md``), vulnerability description
     (leading comments), and the raw code snippet.
  2. Chunk large files into 500-char windows with 50-char overlap.
  3. Embed each chunk with bge-m3 (1024-dim, Infinity /embeddings).
  4. Upsert into the ``threat_intel`` collection with metadata:
     category, severity, ecosystem=EVM, source=defihacklabs, source_url.

Idempotent: point IDs are deterministic UUIDv5 derived from the content hash,
so re-running skips/overwrites the same chunks instead of duplicating them.

Usage:
    python scripts/ingest_defihacklabs.py --dry-run --limit 10
    python scripts/ingest_defihacklabs.py --limit 50
    python scripts/ingest_defihacklabs.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3 loaded
  - git, httpx, numpy
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from rmi_rag.bge_embedders import InfinityBGEEmbedder
from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_defihacklabs")

REPO_URL = "https://github.com/SunWeb3Sec/DeFiHackLabs.git"
REPO_DIR = os.getenv("DEFI_HACKLABS_DIR", "/tmp/defihacklabs")  # noqa: S108
COLLECTION = "threat_intel"
EMBEDDING_DIM = 1024
SOURCE = "defihacklabs"

# Chunking parameters per spec: 500-char windows, 50-char overlap.
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# ── Path-based category mapping ─────────────────────────────────────
# Generic mapping so the script also works if the repo layout changes to
# category directories (e.g. ``access_control/``, ``reentrancy/``).
PATH_CATEGORIES: dict[str, str] = {
    "access_control": "access-control",
    "access-control": "access-control",
    "reentrancy": "reentrancy",
    "flashloan": "flash-loan",
    "flash-loan": "flash-loan",
    "oracle": "oracle-manipulation",
    "oracle_manipulation": "oracle-manipulation",
    "price_manipulation": "oracle-manipulation",
    "rugpull": "rug-pull",
    "rug_pull": "rug-pull",
    "honeypot": "honeypot",
    "mev": "mev",
    "bridge": "bridge-exploit",
    "bridge_exploit": "bridge-exploit",
    "signature": "signature-malleability",
    "signature_malleability": "signature-malleability",
    "integer": "integer-overflow",
    "overflow": "integer-overflow",
    "underflow": "integer-overflow",
    "precision": "precision-loss",
    "logic": "business-logic",
    "business_logic": "business-logic",
    "governance": "governance-attack",
    "flash_loan": "flash-loan",
    "uniswapv2": "amm-manipulation",
    "deflationary": "deflationary-token",
}

# ── Content keyword → category detection (fallback) ─────────────────
CONTENT_CATEGORIES: list[tuple[str, str]] = [
    (r"reentrancy", "reentrancy"),
    (r"flash ?loan", "flash-loan"),
    (r"oracle", "oracle-manipulation"),
    (r"price ?manipulation", "oracle-manipulation"),
    (r"access ?control", "access-control"),
    (r"admin ?privilege", "access-control"),
    (r"onlyOwner", "access-control"),
    (r"honeypot", "honeypot"),
    (r"rug ?pull", "rug-pull"),
    (r"bridge", "bridge-exploit"),
    (r"signature", "signature-malleability"),
    (r"overflow|underflow", "integer-overflow"),
    (r"precision", "precision-loss"),
    (r"governance", "governance-attack"),
    (r"MEV", "mev"),
    (r"deflationary", "deflationary-token"),
    (r"logic", "business-logic"),
]

# ── Severity mapping ────────────────────────────────────────────────
SEVERITY_BY_CATEGORY: dict[str, str] = {
    "reentrancy": "critical",
    "flash-loan": "critical",
    "oracle-manipulation": "critical",
    "access-control": "high",
    "honeypot": "critical",
    "rug-pull": "critical",
    "bridge-exploit": "critical",
    "signature-malleability": "critical",
    "integer-overflow": "high",
    "precision-loss": "medium",
    "business-logic": "high",
    "governance-attack": "critical",
    "mev": "medium",
    "amm-manipulation": "high",
    "deflationary-token": "medium",
}

DEFAULT_SEVERITY = "medium"

_CONTRACT_RE = re.compile(r"\bcontract\s+(\w+)")
_COMMENT_LINES_RE = re.compile(r"^\s*(//|/\*|\*)\s*(.*)$", re.MULTILINE)


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split *text* into fixed-width character windows with overlap."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _content_hash(text: str) -> str:
    """Return a stable content hash (sha256 prefix)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _detect_category_from_path(rel_path: str) -> str | None:
    """Map path segments to a category (handles category-organized layouts)."""
    lowered = rel_path.lower()
    for key, category in PATH_CATEGORIES.items():
        if key in lowered:
            return category
    return None


def _detect_category_from_content(code: str) -> str | None:
    """Detect attack category from Solidity code keywords/comments."""
    for pattern, category in CONTENT_CATEGORIES:
        if re.search(pattern, code, re.IGNORECASE):
            return category
    return None


def _extract_contract_name(code: str) -> str:
    """Extract the first ``contract <Name>`` declaration."""
    match = _CONTRACT_RE.search(code)
    return match.group(1) if match else ""


def _extract_description(code: str) -> str:
    """Extract comment lines at the top of the file as a description."""
    lines: list[str] = []
    for line in code.splitlines()[:80]:
        m = _COMMENT_LINES_RE.match(line)
        if m:
            text = m.group(2).strip()
            if text:
                lines.append(text)
        elif lines:
            # Stop collecting after the leading comment block ends.
            break
    return " ".join(lines)[:2000]


def _sol_files(repo_root: Path) -> list[Path]:
    """Return all ``.sol`` files under ``src/``, skipping the shared helpers."""
    skip_names = {"interface.sol", "basetest.sol", "tokenhelper.sol", "StableMath.sol"}
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return []
    files = sorted(src_dir.rglob("*.sol"))
    return [f for f in files if f.name not in skip_names]


def _parse_incident_metadata(repo_root: Path) -> dict[str, dict[str, str]]:
    """Parse ``past/*/README.md`` incident lists into {protocol: metadata}.

    The READMEs use ``### YYYYMMDD Name - Attack Type`` headers followed by
    ``### Lost: <amount>`` lines. We normalize protocol names (lowercase,
    strip non-alphanumerics) so PoC files like ``Ronin_exp.sol`` can be
    matched to the Ronin Network incident.
    """
    incidents: dict[str, dict[str, str]] = {}
    readme_pattern = re.compile(r"^###\s+(\d{8})\s+(.+?)(?:\s+-\s+(.+))?$")
    lost_pattern = re.compile(r"^###\s+Lost:\s*(.+)$")

    for readme in (repo_root / "past").rglob("README.md"):
        current: dict[str, str] | None = None
        for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            m = readme_pattern.match(line)
            if m:
                date_raw = m.group(1)
                name = m.group(2).strip()
                attack = (m.group(3) or "").strip()
                key = _normalize_name(name)
                if key:
                    current = {
                        "date": date_raw,
                        "protocol": name,
                        "attack_type": attack,
                    }
                    incidents.setdefault(key, current)
                continue
            m = lost_pattern.match(line)
            if m and current:
                current["amount"] = m.group(1).strip()
    return incidents


def _normalize_name(name: str) -> str:
    """Normalize a protocol name for fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _build_doc(
    file: Path,
    repo_root: Path,
    incident_index: dict[str, dict[str, str]],
) -> dict[str, Any] | None:
    """Build a doc dict for a single .sol file."""
    try:
        code = file.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        logger.warning("read_failed path=%s err=%s", file, e)
        return None
    if not code.strip():
        return None

    rel_path = file.relative_to(repo_root).as_posix()
    contract_name = _extract_contract_name(code)
    description = _extract_description(code)

    # Category: path first, then incident metadata, then content keywords.
    category = _detect_category_from_path(rel_path)
    incident = None
    if contract_name:
        incident = incident_index.get(_normalize_name(contract_name))
    if not category and incident and incident.get("attack_type"):
        category = _detect_category_from_path(
            incident["attack_type"]
        ) or _detect_category_from_content(incident["attack_type"])
    if not category:
        category = _detect_category_from_content(code)
    category = category or "unknown"

    severity = SEVERITY_BY_CATEGORY.get(category, DEFAULT_SEVERITY)
    source_url = f"https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/{rel_path}"

    return {
        "doc_id_base": f"defihacklabs:{_normalize_name(rel_path)}",
        "contract_name": contract_name,
        "category": category,
        "severity": severity,
        "source_url": source_url,
        "rel_path": rel_path,
        "incident": incident,
        "code": code,
        "description": description,
    }


async def ingest_files(
    repo_root: Path,
    limit: int | None,
    dry_run: bool,
) -> int:
    """Walk the repo, embed chunks, and upsert into Qdrant."""
    files = _sol_files(repo_root)
    if not files:
        logger.warning("no .sol files found under %s", repo_root / "src")
        return 0
    incident_index = _parse_incident_metadata(repo_root)
    logger.info("sol_files=%d incident_entries=%d", len(files), len(incident_index))

    if limit:
        files = files[:limit]

    # Pre-build docs (no network) so dry-run is cheap.
    docs = []
    for f in files:
        doc = _build_doc(f, repo_root, incident_index)
        if doc:
            docs.append(doc)

    if dry_run:
        total_chunks = sum(len(_chunk_text(d["code"])) for d in docs)
        logger.info(
            "[DRY RUN] would ingest %d files / %d chunks into %s",
            len(docs),
            total_chunks,
            COLLECTION,
        )
        return total_chunks

    embedder = InfinityBGEEmbedder(infinity_url=os.getenv("INFINITY_URL", "http://localhost:8001"))
    store = QdrantVectorStore()
    inserted = 0
    skipped = 0
    total_chunks = 0
    try:
        for i, doc in enumerate(docs, start=1):
            chunks = _chunk_text(doc["code"])
            if not chunks:
                continue
            # Use first chunk as the embedding anchor to keep vector space
            # coherent; each chunk is embedded individually below.
            for ci, chunk in enumerate(chunks):
                total_chunks += 1
                content_hash = _content_hash(chunk)
                doc_id = f"{doc['doc_id_base']}:{content_hash}"
                metadata = {
                    "contract_name": doc["contract_name"],
                    "category": doc["category"],
                    "ecosystem": "EVM",
                    "source": SOURCE,
                    "source_url": doc["source_url"],
                    "rel_path": doc["rel_path"],
                    "chunk_index": ci,
                    "total_chunks": len(chunks),
                    "description": doc["description"][:500],
                }
                if doc["incident"]:
                    metadata.update(
                        {
                            "protocol": doc["incident"].get("protocol", ""),
                            "attack_date": doc["incident"].get("date", ""),
                            "attack_type": doc["incident"].get("attack_type", ""),
                            "amount_lost": doc["incident"].get("amount", ""),
                        }
                    )
                try:
                    embedding = await embedder.embed_one(chunk[:8000])
                except Exception as e:
                    logger.warning("embed_failed doc=%s err=%s", doc_id[:40], str(e)[:120])
                    continue
                if len(embedding) != EMBEDDING_DIM:
                    logger.warning("bad_dim doc=%s dim=%d", doc_id[:40], len(embedding))
                    continue
                ok = await store.insert(
                    doc_id=doc_id,
                    collection=COLLECTION,
                    embedding=embedding,
                    content=chunk,
                    metadata=metadata,
                    source=SOURCE,
                    severity=doc["severity"],
                    chain="EVM",
                )
                if ok:
                    inserted += 1
                else:
                    skipped += 1
            if i % 25 == 0 or i == len(docs):
                logger.info(
                    "progress %d/%d files, %d chunks upserted (%d failed)",
                    i,
                    len(docs),
                    inserted,
                    skipped,
                )
    finally:
        await store.close()
    logger.info(
        "done files=%d chunks_total=%d inserted=%d failed=%d",
        len(docs),
        total_chunks,
        inserted,
        skipped,
    )
    return inserted


def clone_repo() -> Path:
    """Clone (shallow) the DeFiHackLabs repo if not already present."""
    target = Path(REPO_DIR)
    if (target / ".git").exists():
        logger.info("repo already present at %s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", REPO_URL, target)
    tmp = Path(tempfile.mkdtemp(prefix="defihacklabs-clone-"))
    try:
        result = subprocess.run(  # noqa: S603 — REPO_URL is a fixed constant
            ["git", "clone", "--depth", "1", REPO_URL, str(tmp)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr[-500:]}")
        # The clone may be large; keep only src/ + past/ (metadata).
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for sub in ("src", "past"):
            src_sub = tmp / sub
            if src_sub.exists():
                shutil.move(str(src_sub), str(target / sub))
        # .git marker so re-runs skip cloning.
        (target / ".git").mkdir()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    logger.info(
        "clone complete size_mb=%.1f",
        sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) / 1e6,
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest DeFiHackLabs PoCs into threat_intel")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max .sol files to ingest")
    parser.add_argument("--no-clone", action="store_true", help="Skip cloning (use existing dir)")
    args = parser.parse_args()

    repo_root = clone_repo() if not args.no_clone else Path(REPO_DIR)
    if not repo_root.exists():
        logger.error("repo dir missing: %s (run without --no-clone)", repo_root)
        raise SystemExit(1)

    inserted = asyncio.run(ingest_files(repo_root, args.limit, args.dry_run))
    print("=" * 60)
    print(f"DEFI HACKLABS INGEST COMPLETE: {inserted} chunks in {COLLECTION}")
    print("=" * 60)


if __name__ == "__main__":
    main()
