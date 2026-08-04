#!/usr/bin/env python3
"""Ingest Web3 Hack Archive datasets into the RugBrain ``defi_exploits`` collection.

Sources (two complementary archives):
  1. DeFiHackLabs/DeFi-Hack-Chronicle — structured JSON hack events
     (``public/data/hacks/*.json``): title, protocol, blockchain, category,
     ecosystem, estimatedLoss, rootCause, attackVector, lessons, references,
     transactions, attackers, victims.
  2. hubingliang/Web3HackTimeline — ``src/data.json``: 966 dated Web3
     security events with content, themeTags, types, techTags, blockchainTags.

Each event is embedded (1024-dim bge-m3 via Infinity) and upserted into
``defi_exploits`` with canonical metadata (protocol, date, amount_lost_usd,
attack_vector, chain, source, source_url). Point IDs are deterministic
UUIDv5 from content hashes → idempotent re-runs.

Usage:
    python scripts/ingest_web3hack_archive.py --dry-run
    python scripts/ingest_web3hack_archive.py --limit 50
    python scripts/ingest_web3hack_archive.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - Infinity at INFINITY_URL (default http://localhost:8001) with BAAI/bge-m3
  - git, httpx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from rmi_rag.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_web3hack_archive")

# scripts/ dir on sys.path so sibling helpers import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in os.sys.path:
    os.sys.path.insert(0, sys_path_scripts)

from ingest_common import content_hash, embed_many  # noqa: E402

CHRONICLE_URL = "https://github.com/DeFiHackLabs/DeFi-Hack-Chronicle.git"
CHRONICLE_DIR = os.getenv("WEB3HACK_CHRONICLE_DIR", "/tmp/rag-web3hack-chronicle")  # noqa: S108
TIMELINE_URL = "https://github.com/hubingliang/Web3HackTimeline.git"
TIMELINE_DIR = os.getenv("WEB3HACK_TIMELINE_DIR", "/tmp/rag-web3hack-timeline")  # noqa: S108

COLLECTION = "defi_exploits"
SOURCE = "web3hack_archive"
EMBEDDING_DIM = 1024

_CHUNK_SIZE = 2000
_CHUNK_OVERLAP = 100


def _chunk_text(text: str, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split *text* into fixed-width character windows with overlap."""
    text = (text or "").strip()
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


def _normalize_amount(value: Any) -> float:
    """Parse an estimatedLoss totalUSD (int/str/float) to a float."""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value: Any) -> str:
    """Extract an ISO date (YYYY-MM-DD) from a date/datetime value."""
    if isinstance(value, str):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
        return m.group(1) if m else ""
    return ""


def _detect_vector(text: str) -> str:
    """Map free-text keywords to a canonical attack vector."""
    lowered = (text or "").lower()
    for pattern, vector in (
        (r"reentrancy", "reentrancy"),
        (r"flash ?loan", "flash-loan"),
        (r"oracle", "oracle-manipulation"),
        (r"price ?manipulation", "oracle-manipulation"),
        (r"access ?control", "access-control"),
        (r"private ?key", "key-compromise"),
        (r"bridge", "bridge-exploit"),
        (r"governance", "governance-attack"),
        (r"signature", "signature-malleability"),
        (r"overflow|underflow", "integer-overflow"),
        (r"phish", "phishing"),
        (r"rug", "rug-pull"),
        (r"drain", "wallet-drainer"),
    ):
        if re.search(pattern, lowered):
            return vector
    return "unknown"


# ── DeFi-Hack-Chronicle (structured JSON events) ────────────────────────


def _chronicle_events(repo_root: Path) -> list[dict[str, Any]]:
    """Load all structured hack events from DeFi-Hack-Chronicle."""
    events: list[dict[str, Any]] = []
    hacks_dir = repo_root / "public" / "data" / "hacks"
    if not hacks_dir.exists():
        logger.warning("chronicle_hacks_dir_missing %s", hacks_dir)
        return events
    for f in sorted(hacks_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("title"):
                events.append(data)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("chronicle_parse_fail file=%s err=%s", f.name, str(e)[:120])
    return events


def _chronicle_doc(event: dict[str, Any]) -> dict[str, Any]:
    """Build an ingest doc from one structured chronicle event."""
    title = str(event.get("title", ""))
    protocol = str(event.get("protocol", "") or title)
    date = _parse_date(event.get("date") or event.get("attackTime", {}).get("date", ""))
    blockchain = event.get("blockchain") or []
    categories = event.get("category") or []
    ecosystem = str(event.get("ecosystem", "") or "")
    loss = event.get("estimatedLoss") or {}
    amount = _normalize_amount(loss.get("totalUSD"))
    description = str(event.get("description", ""))
    root_cause = str(event.get("rootCause", "") or "")
    attack_vector = str(event.get("attackVector", "") or "")
    lessons = event.get("lessons") or []
    references = event.get("references") or []
    txns = event.get("transactions") or []
    attackers = event.get("attackers") or []

    vector = _detect_vector(f"{title} {attack_vector} {root_cause} {' '.join(categories)}")
    chain = ", ".join(blockchain) if blockchain else ecosystem
    source_url = (
        references[0]
        if references and isinstance(references[0], str)
        else "https://github.com/DeFiHackLabs/DeFi-Hack-Chronicle"
    )

    text = (
        f"Title: {title}\n"
        f"Protocol: {protocol}\n"
        f"Date: {date}\n"
        f"Blockchain: {chain}\n"
        f"Categories: {', '.join(categories)}\n"
        f"Estimated loss: ${amount:,.0f}\n"
        f"Root cause: {root_cause}\n"
        f"Attack vector: {attack_vector or vector}\n"
        f"Description: {description}\n"
        f"Lessons: {'; '.join(str(l) for l in lessons)}\n"
        f"Attacker addresses: {', '.join(str(a) for a in attackers[:3])}"
    )
    metadata = {
        "source_type": SOURCE,
        "sub_source": "chronicle",
        "protocol": protocol,
        "title": title,
        "date": date,
        "date_ts": _date_to_ts(date),
        "chain": chain or "multi",
        "ecosystem": ecosystem or "evm",
        "categories": categories,
        "attack_vector": vector,
        "amount_lost_usd": amount,
        "root_cause": root_cause[:2000],
        "severity": "critical" if amount >= 1e7 else ("high" if amount else "medium"),
        "source_url": source_url,
    }
    return {
        "rel_key": f"chronicle:{event.get('id') or content_hash(title)}",
        "text": text,
        "metadata": metadata,
    }


def _date_to_ts(date_iso: str) -> int:
    """Convert YYYY-MM-DD to a unix timestamp (0 when unparseable)."""
    import datetime as _dt

    if not date_iso:
        return 0
    try:
        return int(
            _dt.datetime.fromisoformat(date_iso).replace(tzinfo=_dt.UTC).timestamp()
        )
    except ValueError:
        return 0


# ── Web3HackTimeline (966 dated events) ────────────────────────────────


def _timeline_events(repo_root: Path) -> list[dict[str, Any]]:
    """Load events from Web3HackTimeline's src/data.json."""
    data_file = repo_root / "src" / "data.json"
    if not data_file.exists():
        logger.warning("timeline_data_missing %s", data_file)
        return []
    try:
        data = json.loads(data_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("timeline_parse_fail err=%s", str(e)[:120])
        return []


def _timeline_doc(event: dict[str, Any], index: int) -> dict[str, Any]:
    """Build an ingest doc from one timeline event (chronological order)."""
    title = str(event.get("title", ""))
    content = str(event.get("content", ""))
    types = event.get("types") or []
    theme_tags = event.get("themeTags") or []
    tech_tags = event.get("techTags") or []
    blockchain_tags = event.get("blockchainTags") or []
    references = event.get("reference") or []
    ref_url = ""
    for r in references:
        if isinstance(r, dict):
            ref_url = str(r.get("link", "")) or ref_url
        elif isinstance(r, str):
            ref_url = r

    vector = _detect_vector(f"{title} {' '.join(types)} {' '.join(theme_tags)} {content[:500]}")
    chain = ", ".join(str(t).strip() for t in blockchain_tags if str(t).strip())
    text = (
        f"Title: {title}\n"
        f"Event type: {', '.join(str(t) for t in types)}\n"
        f"Blockchain: {chain}\n"
        f"Tags: {', '.join(str(t) for t in theme_tags + tech_tags)}\n"
        f"Details: {content}"
    )
    metadata = {
        "source_type": SOURCE,
        "sub_source": "web3hacktimeline",
        "protocol": title,
        "title": title,
        "date": "",
        "date_ts": 0,
        "chain": chain or "multi",
        "categories": [str(t) for t in types],
        "attack_vector": vector,
        "amount_lost_usd": 0.0,
        "severity": "high" if vector != "unknown" else "medium",
        "source_url": ref_url or "https://github.com/hubingliang/Web3HackTimeline",
    }
    return {
        "rel_key": f"timeline:{index}:{content_hash(title)}",
        "text": text,
        "metadata": metadata,
    }


# ── Clone helpers ───────────────────────────────────────────────────────


def _clone(url: str, target: Path, subdirs: tuple[str, ...] | None = None) -> Path:
    """Shallow-clone *url* into *target* (idempotent), keeping only *subdirs*."""
    if (target / ".git").exists() or target.exists():
        logger.info("repo_present dir=%s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", url, target)
    tmp = Path(tempfile.mkdtemp(prefix="web3hack-clone-"))
    try:
        result = subprocess.run(  # noqa: S603 — url is a fixed constant
            ["git", "clone", "--depth", "1", url, str(tmp)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr[-400:]}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        if subdirs:
            for sub in subdirs:
                src_sub = tmp / sub
                if src_sub.exists():
                    shutil.copytree(str(src_sub), str(target / sub))
        else:
            for item in tmp.iterdir():
                if item.name == ".git":
                    continue
                dest = target / item.name
                if item.is_dir():
                    shutil.copytree(str(item), str(dest))
                else:
                    shutil.copy2(str(item), str(dest))
        (target / ".git").mkdir(exist_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    size_mb = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) / 1e6
    logger.info("clone_complete size_mb=%.1f", size_mb)
    return target


# ── Ingest ──────────────────────────────────────────────────────────────


async def ingest_docs(
    client: httpx.AsyncClient,
    docs: list[dict[str, Any]],
    store: QdrantVectorStore,
    dry_run: bool,
) -> tuple[int, int]:
    """Embed + upsert docs into ``defi_exploits``. Returns (inserted, total_chunks)."""
    if dry_run:
        total = sum(len(_chunk_text(d["text"])) for d in docs)
        logger.info(
            "[DRY RUN] would ingest %d docs / %d chunks into %s", len(docs), total, COLLECTION
        )
        return 0, total

    inserted = 0
    total_chunks = 0
    for i, doc in enumerate(docs, start=1):
        chunks = _chunk_text(doc["text"])
        for ci, chunk in enumerate(chunks):
            total_chunks += 1
            doc_id = f"web3hack:{doc['rel_key']}:{ci}:{content_hash(chunk)}"
            metadata = dict(doc["metadata"])
            metadata["chunk_index"] = ci
            vectors = await embed_many(client, [chunk[:8000]])
            if not vectors or not vectors[0]:
                logger.warning("embed_empty doc=%s", doc_id[:50])
                continue
            ok = await store.insert(
                doc_id=doc_id,
                collection=COLLECTION,
                embedding=vectors[0],
                content=chunk,
                metadata=metadata,
                source=SOURCE,
                severity=str(metadata.get("severity", "medium")),
                chain=str(metadata.get("chain", "") or "multi"),
            )
            if ok:
                inserted += 1
        if i % 25 == 0 or i == len(docs):
            logger.info("progress %d/%d docs, %d chunks upserted", i, len(docs), inserted)
    logger.info("done docs=%d chunks=%d inserted=%d", len(docs), total_chunks, inserted)
    return inserted, total_chunks


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    chronicle_root = _clone(CHRONICLE_URL, Path(CHRONICLE_DIR), ("public",))
    timeline_root = _clone(TIMELINE_URL, Path(TIMELINE_DIR))

    chronicle_events = _chronicle_events(chronicle_root)
    timeline_events = _timeline_events(timeline_root)
    logger.info("events chronicle=%d timeline=%d", len(chronicle_events), len(timeline_events))

    docs: list[dict[str, Any]] = [_chronicle_doc(e) for e in chronicle_events]
    docs += [_timeline_doc(e, i) for i, e in enumerate(timeline_events)]
    if args.limit:
        docs = docs[: args.limit]

    store = QdrantVectorStore()
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            inserted, total = await ingest_docs(client, docs, store, args.dry_run)
    finally:
        await store.close()
    return {
        "chronicle": len(chronicle_events),
        "timeline": len(timeline_events),
        "docs": len(docs),
        "inserted": inserted,
        "chunks": total,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Web3 Hack Archive datasets into defi_exploits"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max events to ingest")
    args = parser.parse_args()

    stats = asyncio.run(main_async(args))
    print("=" * 60)
    print(
        f"WEB3 HACK ARCHIVE INGEST COMPLETE: chronicle={stats['chronicle']} "
        f"timeline={stats['timeline']} docs={stats['docs']} inserted={stats['inserted']} "
        f"chunks={stats['chunks']} → {COLLECTION}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
