#!/usr/bin/env python3
"""Ingest crypto scam / exploit / hack knowledge bases into RAG collections.

Pulls public, OSS, regularly-updated datasets and embeds them into Qdrant so
the RAG is an expert on known DeFi hacks, rug pulls, and crypto scams from
day one.

Target collections (one per knowledge type):

    defi_exploits          — specific incidents: how the hack was done, root
                             cause, PoC reproduction, funds lost.
                             Sources: DeFiHackLabs PoCs, rekt-news leaderboard.
    scam_patterns          — scam addresses / URLs / techniques: phishing
                             wallets, fake ICOs, trust-trading, rug-pull signs.
                             Sources: CryptoScamDB blacklist.
    crypto_vulnerabilities — weakness taxonomy + audit knowledge: smart
                             contract weakness classes, audit report findings,
                             attack-surface notes.
                             Sources: SlowMist Knowledge-Base, SWC Registry.

Each chunk carries canonical metadata (see rugbrain.crypto_schema):
source, date (ISO + date_ts), severity, protocol, chain, attack_vector,
amount_lost_usd, source_url — plus a short ``title``.

Pipeline guarantees:
  * Idempotent — deterministic UUIDv5 point IDs derived from source + content
    hash; re-running overwrites the same points instead of duplicating.
  * Checkpointed — progress is tracked in a JSON checkpoint file
    (``RAG_KNOWLEDGE_CHECKPOINT``, default /tmp/rag-knowledge/checkpoint.json)
    keyed by source → file → sha256, so already-ingested files are skipped.
  * Batched — embeddings are computed 50 at a time through
    (OpenAI-compatible /embeddings) and upserts are sent 50 points per call.

Usage:
    python scripts/ingest_scam_knowledge.py --dry-run
    python scripts/ingest_scam_knowledge.py --sources defihacklabs,rekt_news
    python scripts/ingest_scam_knowledge.py --sources all --limit 100
    python scripts/ingest_scam_knowledge.py --force   # ignore checkpoint

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://127.0.0.1:8004/v1) with the embed
    model from RAG_EMBED_MODEL (default qwen3-embedding-8b, 2560-dim)
  - git, httpx, numpy, yaml (PyYAML)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from rugbrain.ingest_smart import (
    dedup_vectors,
    filter_by_severity,
    git_refresh,
    repo_last_updated,
    smart_split,
    sort_by_priority,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_scam_knowledge")

# ── Config ─────────────────────────────────────────────────────────────

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1").rstrip("/")
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")
# Canonical corpus dim: qwen3-embedding-8b is 2560-dim natively, both
# locally (:8004) and on DeepInfra. The old 1024 default rejected every
# vector and looped forever on embed_missing (fixed 2026-09-14).
EMBEDDING_DIM = int(os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or "2560")
KNOWLEDGE_DIR = Path(os.getenv("RAG_KNOWLEDGE_DIR", "/tmp/rag-knowledge"))  # noqa: S108  # nosec B108
CHECKPOINT_PATH = Path(
    os.getenv("RAG_KNOWLEDGE_CHECKPOINT", str(KNOWLEDGE_DIR / "checkpoint.json"))
)
BATCH_SIZE = int(os.getenv("RAG_INGEST_BATCH_SIZE", "50"))
CHUNK_SIZE = 700
CHUNK_OVERLAP = 70

# Collection names (spec: defi_exploits, scam_patterns, crypto_vulnerabilities).
COLLECTION_DEFI_EXPLOITS = "defi_exploits"
COLLECTION_SCAM_PATTERNS = "scam_patterns"
COLLECTION_VULNERABILITIES = "crypto_vulnerabilities"
COLLECTION_CONTRACT_AUDITS = "contract_audits"

# source → collection routing
SOURCE_COLLECTIONS: dict[str, str] = {
    "defihacklabs": COLLECTION_DEFI_EXPLOITS,
    "rekt_news": COLLECTION_DEFI_EXPLOITS,
    "defillama_hacks": COLLECTION_DEFI_EXPLOITS,
    "web3hacks": COLLECTION_DEFI_EXPLOITS,
    "postmortems": COLLECTION_DEFI_EXPLOITS,
    "cryptoscamdb": COLLECTION_SCAM_PATTERNS,
    "slowmist": COLLECTION_VULNERABILITIES,
    "swc_registry": COLLECTION_VULNERABILITIES,
    "immunefi": COLLECTION_VULNERABILITIES,
    "openzeppelin_audits": COLLECTION_CONTRACT_AUDITS,
}

# Chunk deduplication: skip chunks whose top Qdrant hit scores >= this.
DEDUP_THRESHOLD_DEFAULT = 0.95

# ── Controlled vocabulary (mirrors rugbrain.crypto_schema) ──────────────

ATTACK_VECTORS = (
    "reentrancy",
    "flash-loan",
    "oracle-manipulation",
    "access-control",
    "bridge-exploit",
    "governance-attack",
    "signature-malleability",
    "integer-overflow",
    "key-compromise",
    "rug-pull",
    "honeypot",
    "phishing",
    "withdrawal-exploit",
    "denial-of-service",
    "front-running",
    "supply-chain",
    "input-validation",
    "smart-contract-exploit",
    "unknown",
)

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "unknown": 0}

CHAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "ethereum": ("ethereum", "eth ", "erc-20", "erc-721", "evm", "mainnet"),
    "bsc": ("bsc", "binance smart chain", "bnb chain", "bep-20"),
    "solana": ("solana", "spl token", "spl-token"),
    "polygon": ("polygon", "matic"),
    "arbitrum": ("arbitrum", "arb "),
    "optimism": ("optimism", "op mainnet"),
    "base": (" base ", "base network"),
    "avalanche": ("avalanche", "avax"),
    "tron": ("tron", "trc-20"),
    "fantom": ("fantom", " ftm "),
    "multi": ("multi-chain", "cross-chain", "multiple chains"),
}

# keyword → attack vector (best-effort, used when structured metadata is absent)
_CONTENT_VECTORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"reentranc", re.I), "reentrancy"),
    (re.compile(r"flash\s?loan", re.I), "flash-loan"),
    (re.compile(r"oracle", re.I), "oracle-manipulation"),
    (re.compile(r"price\s?manipulation", re.I), "oracle-manipulation"),
    (re.compile(r"access\s?control", re.I), "access-control"),
    (re.compile(r"onlyowner|admin\s?privilege", re.I), "access-control"),
    (re.compile(r"bridge", re.I), "bridge-exploit"),
    (re.compile(r"governance", re.I), "governance-attack"),
    (re.compile(r"signature", re.I), "signature-malleability"),
    (re.compile(r"overflow|underflow", re.I), "integer-overflow"),
    (re.compile(r"rug\s?pull|rugpull", re.I), "rug-pull"),
    (re.compile(r"honeypot", re.I), "honeypot"),
    (re.compile(r"phish", re.I), "phishing"),
    (re.compile(r"private\s?key|seed\s?phrase", re.I), "key-compromise"),
    (re.compile(r"withdraw", re.I), "withdrawal-exploit"),
    (re.compile(r"denial\s?of\s?service|dos\b", re.I), "denial-of-service"),
    (re.compile(r"front\s?run", re.I), "front-running"),
    (re.compile(r"supply\s?chain", re.I), "supply-chain"),
    (re.compile(r"input\s?valid", re.I), "input-validation"),
]


def _detect_attack_vector(text: str) -> str:
    for pattern, vector in _CONTENT_VECTORS:
        if pattern.search(text or ""):
            return vector
    return "unknown"


def _detect_chain(text: str) -> str:
    lowered = f" {text.lower()} "
    for chain, aliases in CHAIN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                return chain
    return ""


def _normalize_severity(value: Any) -> str:
    syn: dict[str, str] = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "moderate": "medium",
        "low": "low",
        "info": "info",
        "informational": "info",
        "unknown": "unknown",
    }
    key = str(value or "").strip().lower()
    if key in syn:
        return syn[key]
    if "crit" in key:
        return "critical"
    if "high" in key:
        return "high"
    if "medium" in key or "moderate" in key:
        return "medium"
    if "low" in key:
        return "low"
    return "unknown"


def _date_to_ts(date_iso: str) -> float | None:
    """Parse YYYY-MM-DD (or YYYYMMDD / MM/DD/YYYY) into a unix timestamp."""
    if not date_iso:
        return None
    s = str(date_iso).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
    return None


def _normalize_date(date_iso: Any) -> str:
    s = str(date_iso or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _parse_amount_lost(text: Any) -> float | None:
    """Parse '$200M', '$3.5 billion', '2000000', '$1.2m' → USD float."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).replace(",", "").replace("$", "").strip().lower()
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*(b|bn|billion|m|mn|million|k|thousand|t|trillion)?", s)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    mult = {
        "b": 1e9,
        "bn": 1e9,
        "billion": 1e9,
        "t": 1e12,
        "trillion": 1e12,
        "m": 1e6,
        "mn": 1e6,
        "million": 1e6,
        "k": 1e3,
        "thousand": 1e3,
    }
    suffix = m.group(2) or ""
    if suffix in mult:
        value *= mult[suffix]
    elif value < 1000:
        # bare number without suffix is already in USD
        pass
    return round(value, 2)


# ── Chunking ───────────────────────────────────────────────────────────


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into fixed-width character windows with overlap."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
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


def _chunk_markdown_sections(text: str, max_len: int = CHUNK_SIZE) -> list[str]:
    """Split markdown into sections by headings, then window long sections."""
    text = (text or "").strip()
    if not text:
        return []
    sections = re.split(r"(?m)^(?=#{1,3}\s)", text)
    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= max_len:
            chunks.append(section)
        else:
            chunks.extend(_chunk_text(section, size=max_len))
    return chunks


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _point_id(source: str, rel_key: str, content_hash: str) -> str:
    """Deterministic UUIDv5 point ID — idempotent across re-runs."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"rag:{source}:{rel_key}:{content_hash}"))


# ── Checkpoint ─────────────────────────────────────────────────────────


class Checkpoint:
    """JSON checkpoint tracking which source files have been ingested.

    Layout: {"sources": {"<source>": {"<rel_key>": "<sha256>"}},
             "last_updated": {"<source>": "<iso>"}}.
    A file is skipped when its current sha256 matches the checkpoint.
    """

    def __init__(self, path: Path):
        self.path = path
        # Layout: {"sources": {"<source>": {"<rel_key>": "<sha256>"}},
        #          "last_updated": {"<source>": "<iso>"}}
        self.data: dict[str, Any] = {"sources": {}, "last_updated": {}}

    def load(self) -> None:
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw_sources = loaded.get("sources")
                    if isinstance(raw_sources, dict):
                        cleaned: dict[str, dict[str, str]] = {}
                        for src, files in raw_sources.items():
                            if isinstance(src, str) and isinstance(files, dict):
                                cleaned[src] = {
                                    str(k): str(v) for k, v in files.items() if isinstance(k, str)
                                }
                        self.data["sources"] = cleaned
                    raw_last = loaded.get("last_updated")
                    if isinstance(raw_last, dict):
                        self.data["last_updated"] = {
                            str(k): str(v) for k, v in raw_last.items() if isinstance(k, str)
                        }
            except (json.JSONDecodeError, OSError, TypeError):
                logger.warning("checkpoint unreadable, starting fresh: %s", self.path)
                self.data = {"sources": {}, "last_updated": {}}
        self.data.setdefault("sources", {})
        self.data.setdefault("last_updated", {})

    def is_done(self, source: str, rel_key: str, text: str) -> bool:
        current = self.data["sources"].get(source, {}).get(rel_key)
        return bool(current) and current == _content_hash(text)

    def mark(self, source: str, rel_key: str, text: str) -> None:
        self.data.setdefault("sources", {})
        self.data["sources"].setdefault(source, {})[rel_key] = _content_hash(text)

    def set_last_updated(self, source: str, stamp: str) -> None:
        self.data.setdefault("last_updated", {})
        self.data["last_updated"][source] = stamp

    def get_last_updated(self, source: str) -> str:
        return str(self.data.get("last_updated", {}).get(source, ""))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


# ── Embedding (batched, 50 at a time) ─────────────────────────────────


async def _embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed texts on the bulk lane (DeepInfra-first, local fallback).

    Returns L2-normalized vectors aligned with input order (None on failure).
    The lane lives in ``scripts/embed_lane.py`` so heavy ingestion does not
    saturate the local llama.cpp server.
    """
    if not texts:
        return []
    from embed_lane import embed_batch as _lane_embed_batch

    vectors = await _lane_embed_batch(texts)
    results: list[list[float] | None] = []
    for vec in vectors:
        if vec is None:
            results.append(None)
            continue
        arr = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(arr))
        if n > 0:
            arr = arr / n
        results.append(arr.tolist())
    return results


# ── Qdrant (batched upsert) ────────────────────────────────────────────


async def _ensure_collection(client: httpx.AsyncClient, name: str) -> bool:
    try:
        r = await client.get(f"/collections/{name}")
        if r.status_code == 200:
            return True
    except Exception:
        logger.debug("ensure_collection_check_failed %s", name)
    try:
        payload = {"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}}
        r = await client.put(f"/collections/{name}", json=payload)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error("create_collection_fail %s: %s", name, e)
        return False


async def _upsert_batch(
    client: httpx.AsyncClient,
    collection: str,
    points: list[dict[str, Any]],
) -> int:
    """Upsert up to 50 points in one REST call. Returns success count."""
    if not points:
        return 0
    ok = 0
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i : i + BATCH_SIZE]
        try:
            r = await client.put(f"/collections/{collection}/points", json={"points": batch})
            if r.status_code in (200, 201):
                ok += len(batch)
            else:
                logger.warning("upsert_fail status=%d body=%s", r.status_code, r.text[:300])
        except Exception as e:
            logger.warning("upsert_error: %s", e)
    return ok


def _build_point(
    source: str,
    collection: str,
    rel_key: str,
    content: str,
    metadata: dict[str, Any],
    vector: list[float],
) -> dict[str, Any]:
    point_id = _point_id(source, rel_key, _content_hash(content))
    payload = {
        "content": content[:10000],
        "metadata": metadata or {},
        "source": source,
        "severity": _normalize_severity(metadata.get("severity", "unknown")),
        "chain": metadata.get("chain", "") or "",
        "collection": collection,
    }
    # Surface structured fields at payload top-level so /api/v1/search
    # filters (severity/chain/attack_vector/protocol/source_type) work.
    for key in ("attack_vector", "protocol", "source_type", "date", "date_ts", "amount_lost_usd"):
        if metadata.get(key) is not None:
            payload[key] = metadata[key]
    return {"id": point_id, "vector": vector, "payload": payload}


# ── Source parsers ─────────────────────────────────────────────────────


def _clone_repo(url: str, name: str, subdirs: tuple[str, ...] | None = None) -> Path:
    """Shallow-clone *url* into KNOWLEDGE_DIR/<name> keeping only *subdirs*."""
    target = KNOWLEDGE_DIR / name
    if (target / ".git").exists():
        logger.info("repo already present: %s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    logger.info("cloning %s → %s (shallow)", url, target)
    tmp = Path(tempfile.mkdtemp(prefix=f"{name}-clone-"))
    try:
        result = subprocess.run(  # noqa: S603 — url is a fixed constant from the source table
            ["git", "clone", "--depth", "1", url, str(tmp)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr[-500:]}")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        if subdirs:
            for sub in subdirs:
                src_sub = tmp / sub
                if src_sub.exists():
                    shutil.move(str(src_sub), str(target / sub))
        else:
            for item in tmp.iterdir():
                if item.name == ".git":
                    continue
                shutil.move(str(item), str(target / item.name))
        (target / ".git").mkdir()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    logger.info("clone complete name=%s", name)
    return target


# ── DeFiHackLabs ───────────────────────────────────────────────────────

_CONTRACT_RE = re.compile(r"\bcontract\s+(\w+)")
_COMMENT_LINES_RE = re.compile(r"^\s*(//|/\*|\*)\s*(.*)$", re.MULTILINE)
_SKIP_SOL = {"interface.sol", "basetest.sol", "tokenhelper.sol", "StableMath.sol"}


def _extract_description(code: str) -> str:
    """Extract comment lines at the top of a Solidity file as a description."""
    lines: list[str] = []
    for line in code.splitlines()[:80]:
        m = _COMMENT_LINES_RE.match(line)
        if m:
            text = m.group(2).strip()
            if text:
                lines.append(text)
        elif lines:
            break
    return " ".join(lines)[:1000]


def _parse_defihacklabs_incidents(repo_root: Path) -> dict[str, dict[str, str]]:
    """Parse past/*/README.md incident lists → {normalized protocol: metadata}."""
    incidents: dict[str, dict[str, str]] = {}
    readme_pattern = re.compile(r"^###\s+(\d{8})\s+(.+?)(?:\s+-\s+(.+))?$")
    lost_pattern = re.compile(r"^###\s+Lost:\s*(.+)$")
    for readme in (repo_root / "past").rglob("README.md"):
        current: dict[str, str] | None = None
        for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            m = readme_pattern.match(line)
            if m:
                key = re.sub(r"[^a-z0-9]+", "", m.group(2).lower())
                if key:
                    current = {
                        "date": m.group(1),
                        "protocol": m.group(2).strip(),
                        "attack_type": (m.group(3) or "").strip(),
                    }
                    incidents.setdefault(key, current)
                continue
            m = lost_pattern.match(line)
            if m and current:
                current["amount"] = m.group(1).strip()
    return incidents


def _defihacklabs_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Build one doc per PoC chunk, merging incident metadata in.

    Files under ~4KB are kept whole; larger PoCs are split into overlapping
    windows so each chunk is individually searchable (a 300-line PoC would
    otherwise exceed embedding context limits).
    """
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return []
    files = sorted(src_dir.rglob("*.sol"))
    files = [f for f in files if f.name not in _SKIP_SOL]
    if limit:
        files = files[:limit]
    incidents = _parse_defihacklabs_incidents(repo_root)
    docs: list[dict[str, Any]] = []
    for f in files:
        try:
            code = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not code.strip():
            continue
        rel_path = f.relative_to(repo_root).as_posix()
        contract_match = _CONTRACT_RE.search(code)
        contract_name = contract_match.group(1) if contract_match else ""
        incident = (
            incidents.get(re.sub(r"[^a-z0-9]+", "", contract_name.lower()))
            if contract_name
            else None
        )
        attack = incident.get("attack_type", "") if incident else ""
        vector = _detect_attack_vector(f"{rel_path} {attack} {code[:2000]}")
        chain = _detect_chain(f"{rel_path} {attack}")
        amount = _parse_amount_lost(incident.get("amount")) if incident else None
        title = f"DeFi exploit PoC: {contract_name or f.name}"
        description = _extract_description(code)
        base_metadata: dict[str, Any] = {
            "source_type": "defihacklabs",
            "protocol": (incident or {}).get("protocol", "") or contract_name,
            "contract_name": contract_name,
            "date": _normalize_date((incident or {}).get("date")),
            "attack_vector": vector,
            "chain": chain or "EVM",
            "amount_lost_usd": amount,
            "severity": "critical" if amount and amount >= 1e7 else "high",
            "source_url": f"https://github.com/SunWeb3Sec/DeFiHackLabs/blob/main/{rel_path}",
            "rel_path": rel_path,
        }
        if incident:
            base_metadata["attack_description"] = attack
        chunks = _chunk_text(code)
        for ci, chunk in enumerate(chunks):
            chunk_key = f"{rel_path}:{ci}" if len(chunks) > 1 else rel_path
            meta = dict(base_metadata)
            if description:
                meta["description"] = description[:500]
            docs.append(
                {
                    "rel_key": f"defihacklabs:{chunk_key}",
                    "title": title,
                    "text": chunk,
                    "metadata": meta,
                }
            )
    return docs


# ── rekt-news leaderboard ──────────────────────────────────────────────

REKT_LEADERBOARD_URL = "https://rekt.news/leaderboard"


async def _fetch_rekt_leaderboard() -> list[dict[str, Any]]:
    """Fetch the rekt.news leaderboard and extract its __NEXT_DATA__ JSON."""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
        r = await c.get(REKT_LEADERBOARD_URL, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    html = r.text
    idx = html.find("__NEXT_DATA__")
    if idx < 0:
        raise RuntimeError("rekt.news leaderboard: __NEXT_DATA__ not found")
    start = html.find(">", idx) + 1
    end = html.find("</script>", start)
    data = json.loads(html[start:end])
    leaderboard = data.get("props", {}).get("pageProps", {}).get("leaderboard", [])
    return leaderboard or []


def _rekt_docs(entries: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    if limit:
        entries = entries[:limit]
    docs: list[dict[str, Any]] = []
    for entry in entries:
        slug = str(entry.get("slug", ""))
        title = str(entry.get("title", "")).strip()
        excerpt = str(entry.get("excerpt", "")).strip()
        rekt = entry.get("rekt", {}) or {}
        amount = _parse_amount_lost(rekt.get("amount")) if rekt.get("amount") is not None else None
        date_raw = rekt.get("date") or entry.get("date")
        date_iso = _normalize_date(date_raw)
        tags = entry.get("tags", []) or []
        tags_text = ", ".join(str(t) for t in tags)
        vector = _detect_attack_vector(f"{title} {tags_text} {excerpt}")
        chain = _detect_chain(f"{title} {tags_text} {excerpt}")
        text = (
            f"Title: {title}\n"
            f"Date: {date_iso}\n"
            f"Funds lost: {amount if amount is not None else 'unknown'}\n"
            f"Tags: {tags_text}\n"
            f"Excerpt: {excerpt}"
        )
        docs.append(
            {
                "rel_key": f"rekt_news:{slug}",
                "title": title,
                "text": text,
                "metadata": {
                    "source_type": "rekt_news",
                    "protocol": title,
                    "date": date_iso,
                    "date_ts": _date_to_ts(date_iso),
                    "attack_vector": vector,
                    "chain": chain,
                    "amount_lost_usd": amount,
                    "severity": "critical"
                    if amount and amount >= 1e7
                    else ("high" if amount else "medium"),
                    "source_url": f"https://rekt.news/{slug}/",
                    "slug": slug,
                },
            }
        )
    return docs


# ── CryptoScamDB blacklist ─────────────────────────────────────────────

_YAML_KEY_RE = re.compile(r"^\s*name:\s*(.+)$")
_YAML_URL_RE = re.compile(r"^\s*url:\s*(.+)$")
_YAML_CAT_RE = re.compile(r"^\s*category:\s*(.+)$")
_YAML_SUB_RE = re.compile(r"^\s*subcategory:\s*(.+)$")
_YAML_DESC_RE = re.compile(r"^\s*description:\s*(.+)$")
_YAML_ADDR_RE = re.compile(r'^\s*-\s*"?(0x[a-fA-F0-9]{20,})"?\s*$')


def _parse_yaml_entries(text: str, source_file: str, source_url: str) -> list[dict[str, Any]]:
    """Lightweight YAML parse — the blacklist files are simple list-of-dicts.

    We avoid a hard PyYAML dependency in prod paths; if PyYAML is available it
    is used as a more robust fallback (see _cryptoscamdb_docs).
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in text.splitlines():
        m = _YAML_KEY_RE.match(line)
        if m:
            if current:
                entries.append(current)
            current = {"_file": source_file, "_url": source_url, "name": m.group(1).strip()}
            continue
        if not current:
            continue
        m = _YAML_URL_RE.match(line)
        if m:
            current["url"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_CAT_RE.match(line)
        if m:
            current["category"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_SUB_RE.match(line)
        if m:
            current["subcategory"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_DESC_RE.match(line)
        if m:
            current["description"] = m.group(1).strip().strip('"')
            continue
        m = _YAML_ADDR_RE.match(line)
        if m:
            current.setdefault("addresses", []).append(m.group(1))
            continue
    if current:
        entries.append(current)
    return entries


def _cryptoscamdb_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Parse urls.yaml + uris.yaml (scam URLs/accounts) into pattern docs."""
    docs: list[dict[str, Any]] = []
    data_dir = repo_root / "data"
    if not data_dir.exists():
        return []
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is None:
        logger.info("PyYAML unavailable — using lightweight YAML parser for cryptoscamdb")
    for filename in ("urls.yaml", "uris.yaml"):
        path = data_dir / filename
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        entries: list[dict[str, Any]] = []
        if yaml is not None:
            try:
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
                entries = []
        if not entries:
            entries = _parse_yaml_entries(
                raw,
                filename,
                f"https://github.com/CryptoScamDB/blacklist/blob/master/data/{filename}",
            )
        if limit:
            entries = entries[:limit]
        for entry in entries:
            name = str(entry.get("name", ""))
            url = str(entry.get("url", ""))
            category = str(entry.get("category", ""))
            subcategory = str(entry.get("subcategory", ""))
            description = str(entry.get("description", ""))
            addresses = entry.get("_addresses") or entry.get("addresses") or []
            vector = _detect_attack_vector(f"{name} {category} {subcategory} {description}")
            if "phish" in category.lower():
                vector = "phishing"
            chain = _detect_chain(f"{name} {subcategory} {description}")
            text = (
                f"Scam entry: {name}\n"
                f"URL: {url}\n"
                f"Category: {category} ({subcategory})\n"
                f"Description: {description}\n"
                f"Associated addresses: {', '.join(addresses[:10])}"
            )
            rel_key = f"cryptoscamdb:{name or url}"
            docs.append(
                {
                    "rel_key": rel_key,
                    "title": f"Crypto scam: {name or url}",
                    "text": text,
                    "metadata": {
                        "source_type": "cryptoscamdb",
                        "protocol": subcategory or name,
                        "scam_type": category,
                        "attack_vector": vector,
                        "chain": chain,
                        "severity": "high"
                        if category.lower() in ("scamming", "malware", "fake ico")
                        else "medium",
                        "source_url": f"https://github.com/CryptoScamDB/blacklist/blob/master/data/{filename}",
                        "addresses": addresses[:10],
                        "url": url,
                    },
                }
            )
    return docs


# ── SlowMist Knowledge-Base ────────────────────────────────────────────

_SLOWMIST_MD_FILES = (
    (
        "blockchain_security_study_notes",
        "https://github.com/slowmist/Knowledge-Base/blob/master/blockchain_security_study_notes/{f}",
    ),
    ("translations", "https://github.com/slowmist/Knowledge-Base/blob/master/translations/{f}"),
    ("open-report-V2", "https://github.com/slowmist/Knowledge-Base/blob/master/open-report-V2/{f}"),
)


def _slowmist_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Parse SlowMist markdown knowledge (study notes, audit reports, translations)."""
    docs: list[dict[str, Any]] = []
    for subdir, url_tpl in _SLOWMIST_MD_FILES:
        base = repo_root / subdir
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if not text.strip():
                continue
            rel = f.relative_to(repo_root).as_posix()
            rel_key = f"slowmist:{rel}"
            title = f.stem.replace("-", " ").replace("_", " ").strip()
            vector = _detect_attack_vector(text[:4000])
            chain = _detect_chain(f"{rel} {text[:2000]}")
            # Source-type: audit report vs study note
            if "audit report" in text.lower()[:400] or "audit" in rel.lower():
                source_type = "audit_report"
            elif "translation" in rel:
                source_type = "translation"
            else:
                source_type = "study_note"
            chunks = _chunk_markdown_sections(text)
            for ci, chunk in enumerate(chunks):
                chunk_key = rel_key if len(chunks) == 1 else f"{rel_key}:{ci}"
                docs.append(
                    {
                        "rel_key": chunk_key,
                        "title": f"{title} ({source_type})",
                        "text": chunk,
                        "metadata": {
                            "source_type": source_type,
                            "protocol": "",
                            "attack_vector": vector,
                            "chain": chain,
                            "severity": "medium",
                            "source_url": url_tpl.format(f=rel),
                            "rel_path": rel,
                        },
                    }
                )
            if limit and len(docs) >= limit:
                return docs
    return docs


# ── SWC Registry ───────────────────────────────────────────────────────

_SWC_MD = "https://raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/entries/docs/{swc}.md"
_SWC_IDS = [f"SWC-{i}" for i in range(100, 137)]

_SWC_VECTORS: dict[str, str] = {
    "SWC-100": "access-control",
    "SWC-101": "integer-overflow",
    "SWC-102": "supply-chain",
    "SWC-103": "supply-chain",
    "SWC-104": "input-validation",
    "SWC-105": "access-control",
    "SWC-106": "access-control",
    "SWC-107": "reentrancy",
    "SWC-108": "access-control",
    "SWC-109": "smart-contract-exploit",
    "SWC-110": "input-validation",
    "SWC-111": "supply-chain",
    "SWC-112": "access-control",
    "SWC-113": "denial-of-service",
    "SWC-114": "front-running",
    "SWC-115": "access-control",
    "SWC-116": "oracle-manipulation",
    "SWC-117": "signature-malleability",
    "SWC-118": "access-control",
    "SWC-119": "smart-contract-exploit",
    "SWC-120": "oracle-manipulation",
    "SWC-121": "signature-malleability",
    "SWC-122": "smart-contract-exploit",
    "SWC-123": "input-validation",
    "SWC-124": "smart-contract-exploit",
    "SWC-125": "smart-contract-exploit",
    "SWC-126": "denial-of-service",
    "SWC-127": "smart-contract-exploit",
    "SWC-128": "denial-of-service",
    "SWC-129": "smart-contract-exploit",
    "SWC-130": "input-validation",
    "SWC-131": "smart-contract-exploit",
    "SWC-132": "smart-contract-exploit",
    "SWC-133": "signature-malleability",
    "SWC-134": "smart-contract-exploit",
    "SWC-135": "smart-contract-exploit",
    "SWC-136": "supply-chain",
}


async def _swc_docs(limit: int | None) -> list[dict[str, Any]]:
    """Fetch SWC registry markdown entries (weakness taxonomy)."""
    ids = _SWC_IDS[:limit] if limit else _SWC_IDS
    docs: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        for swc in ids:
            try:
                r = await c.get(_SWC_MD.format(swc=swc))
                if r.status_code != 200:
                    continue
                text = r.text
            except Exception as e:
                logger.warning("swc_fetch_fail %s: %s", swc, str(e)[:100])
                continue
            title = swc
            m = re.search(r"^#\s+(.+)$", text, re.M)
            if m:
                title = m.group(1).strip()
            vector = _SWC_VECTORS.get(swc, _detect_attack_vector(text[:2000]))
            chunks = _chunk_markdown_sections(text)
            for ci, chunk in enumerate(chunks):
                chunk_key = f"swc:{swc}:{ci}" if len(chunks) > 1 else f"swc:{swc}"
                docs.append(
                    {
                        "rel_key": chunk_key,
                        "title": f"{swc} — {title}",
                        "text": chunk,
                        "metadata": {
                            "source_type": "swc_registry",
                            "protocol": swc,
                            "attack_vector": vector,
                            "chain": "EVM",
                            "severity": "high",
                            "source_url": f"https://github.com/SmartContractSecurity/SWC-registry/blob/master/entries/{swc}.md",
                        },
                    }
                )
    return docs


# ── DeFiLlama hacks database ────────────────────────────────────────────

DEFILLAMA_HACKS_API = "https://api.llama.fi/hacks"


async def _defillama_hacks_docs(limit: int | None) -> list[dict[str, Any]]:
    """Ingest the DeFiLlama hacks database (600+ incidents).

    Finishes the Rekt/exploit database coverage with a structured,
    maintained incident database (date, amount, technique, classification,
    chain, language).
    """
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.get(DEFILLAMA_HACKS_API)
            if r.status_code != 200:
                logger.warning("defillama_hacks_fetch status=%d", r.status_code)
                return docs
            hacks = r.json() or []
    except Exception as e:
        logger.warning("defillama_hacks_error: %s", str(e)[:150])
        return docs
    if limit:
        hacks = hacks[:limit]
    for hack in hacks:
        if not isinstance(hack, dict):
            continue
        ts = float(hack.get("date") or 0)
        date_iso = datetime.fromtimestamp(ts, tz=UTC).date().isoformat() if ts > 0 else ""
        name = str(hack.get("name", "")).strip() or "unknown"
        amount = float(hack.get("amount") or 0)
        chains = hack.get("chain") or []
        chain = str(chains[0]).lower() if isinstance(chains, list) and chains else "EVM"
        technique = str(hack.get("technique") or "").strip()
        classification = str(hack.get("classification") or "").strip()
        vector = _detect_attack_vector(f"{name} {technique} {classification}")
        text = (
            f"Title: {name}\n"
            f"Date: {date_iso or 'unknown'}\n"
            f"Funds lost: {amount if amount else 'unknown'}\n"
            f"Technique: {technique or 'unknown'}\n"
            f"Classification: {classification or 'unknown'}\n"
            f"Chain: {chain}\n"
            f"Language: {hack.get('language') or 'unknown'}"
        )
        docs.append(
            {
                "rel_key": (
                    f"defillama_hacks:{re.sub(r'[^a-z0-9]+', '-', name.lower())}:{date_iso}"
                ),
                "title": f"DeFi hack: {name}",
                "text": text,
                "metadata": {
                    "source_type": "defillama_hacks",
                    "protocol": name,
                    "date": date_iso,
                    "date_ts": _date_to_ts(date_iso),
                    "attack_vector": vector,
                    "chain": chain,
                    "amount_lost_usd": amount if amount else None,
                    "severity": (
                        "critical" if amount >= 1e7 else ("high" if amount >= 1e6 else "medium")
                    ),
                    "source_url": "https://defillama.com/hacks",
                    "technique": technique,
                    "classification": classification,
                },
            }
        )
    logger.info("defillama_hacks_docs=%d", len(docs))
    return docs


# ── Web3Hacks: Cyfrin/sc-exploits-minimized PoCs ────────────────────────


def _web3hacks_docs(repo_root: Path, limit: int | None) -> list[dict[str, Any]]:
    """Parse Cyfrin ``sc-exploits-minimized`` Foundry PoCs (src/*.sol).

    A maintained, public collection of minimized smart-contract exploits —
    each file is a reproducible exploit PoC, ideal for exploit-pattern RAG.
    """
    docs: list[dict[str, Any]] = []
    src_dir = repo_root / "src"
    if not src_dir.exists():
        return []
    files = sorted(src_dir.rglob("*.sol"))
    files = [f for f in files if f.name not in _SKIP_SOL]
    if limit:
        files = files[:limit]
    for f in files:
        try:
            code = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not code.strip():
            continue
        rel_path = f.relative_to(repo_root).as_posix()
        contract_match = _CONTRACT_RE.search(code)
        contract_name = contract_match.group(1) if contract_match else f.stem
        description = _extract_description(code)
        vector = _detect_attack_vector(f"{rel_path} {code[:2000]}")
        chain = _detect_chain(rel_path)
        title = f"Web3 exploit PoC: {contract_name}"
        base_metadata: dict[str, Any] = {
            "source_type": "web3hacks",
            "protocol": contract_name,
            "contract_name": contract_name,
            "attack_vector": vector,
            "chain": chain or "EVM",
            "severity": "high",
            "source_url": (f"https://github.com/Cyfrin/sc-exploits-minimized/blob/main/{rel_path}"),
            "rel_path": rel_path,
        }
        chunks = smart_split(code)
        for ci, chunk in enumerate(chunks):
            chunk_key = f"{rel_path}:{ci}" if len(chunks) > 1 else rel_path
            meta = dict(base_metadata)
            if description:
                meta["description"] = description[:500]
            docs.append(
                {
                    "rel_key": f"web3hacks:{chunk_key}",
                    "title": title,
                    "text": chunk,
                    "metadata": meta,
                }
            )
    logger.info("web3hacks_docs=%d", len(docs))
    return docs


# ── Immunefi bug bounty programs ────────────────────────────────────────

IMMUNEFI_EXPLORE_URL = "https://immunefi.com/explore/"
_IMMUNEFI_STOPWORDS = {
    "bounty",
    "rewards",
    "overview",
    "audit",
    "live",
    "launch",
    "view all",
    "read more",
    "loading",
    "explore",
    "immunefi",
    "sign in",
    "get started",
    "dashboard",
}


async def _immunefi_docs(limit: int | None) -> list[dict[str, Any]]:
    """Scrape the Immunefi bug-bounty explore page (best-effort).

    The page is a Next.js RSC app; we pull text fragments from its
    ``self.__next_f.push`` chunks and extract bounty program names. This is
    intentionally conservative: if the page format changes, the source
    degrades to a warning instead of failing the whole ingest.
    """
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
            r = await c.get(IMMUNEFI_EXPLORE_URL, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                logger.warning("immunefi_fetch status=%d", r.status_code)
                return docs
            html = r.text
    except Exception as e:
        logger.warning("immunefi_error: %s", str(e)[:150])
        return docs

    fragments: list[str] = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html)
    text = " ".join(fragments)
    candidates = re.findall(
        r"([A-Z][A-Za-z0-9][A-Za-z0-9 .&'-]{2,60}?)\s*(?:bug bounty|bounty)", text
    )
    candidates = [
        c.strip()
        for c in candidates
        if len(c.strip()) >= 4 and c.strip().lower() not in _IMMUNEFI_STOPWORDS
    ]
    # Deduplicate preserving order.
    candidates = list(dict.fromkeys(candidates))
    if limit:
        candidates = candidates[:limit]
    for name in candidates:
        vector = _detect_attack_vector(name)
        docs.append(
            {
                "rel_key": f"immunefi:{re.sub(r'[^a-z0-9]+', '-', name.lower())}",
                "title": f"Immunefi bounty program: {name}",
                "text": (
                    f"Immunefi bug bounty program: {name}. In-scope vulnerability "
                    "research can earn bounties; attack surfaces and vulnerability "
                    "classes are documented on the program page."
                ),
                "metadata": {
                    "source_type": "immunefi",
                    "protocol": name,
                    "attack_vector": vector,
                    "chain": "multi",
                    "severity": "medium",
                    "source_url": IMMUNEFI_EXPLORE_URL,
                },
            }
        )
    if not docs:
        logger.warning("immunefi_parse_empty — page format may have changed")
    logger.info("immunefi_docs=%d", len(docs))
    return docs


# ── OpenZeppelin audit reports (via Solodit listing) ────────────────────

SOLODIT_URL = "https://solodit.xyz"


async def _solodit_docs(limit: int | None) -> list[dict[str, Any]]:
    """Scrape audit report listings from Solodit (best-effort).

    Solodit aggregates public smart-contract audit reports — including
    OpenZeppelin, Code4rena, Spearbit and many others — into one listing.
    We extract report titles/links from the rendered HTML.
    """
    docs: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
            r = await c.get(SOLODIT_URL, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200:
                logger.warning("solodit_fetch status=%d", r.status_code)
                return docs
            html = r.text
    except Exception as e:
        logger.warning("solodit_error: %s", str(e)[:150])
        return docs

    anchors = re.findall(r'<a[^>]*href="(/reports/[^"]+)"[^>]*>([^<]{5,120})</a>', html)
    seen: set[str] = set()
    count = 0
    for href, title in anchors:
        title = re.sub(r"\s+", " ", title).strip()
        if not title or title.lower() in seen:
            continue
        if any(w in title.lower() for w in ("read more", "view all", "loading", "load more")):
            continue
        seen.add(title.lower())
        count += 1
        vector = _detect_attack_vector(title)
        docs.append(
            {
                "rel_key": f"openzeppelin_audits:{re.sub(r'[^a-z0-9]+', '-', title.lower())}",
                "title": f"Audit report: {title}",
                "text": (
                    f"Public smart-contract audit report: {title}. "
                    "Audit reports document findings, vulnerability classes, "
                    "and fixes for reviewed protocols."
                ),
                "metadata": {
                    "source_type": "openzeppelin_audits",
                    "protocol": title.split(" by ")[0].strip(),
                    "attack_vector": vector,
                    "chain": "multi",
                    "severity": "medium",
                    "source_url": f"{SOLODIT_URL}{href}",
                },
            }
        )
        if limit and count >= limit:
            break
    if not docs:
        logger.warning("solodit_parse_empty — page format may have changed")
    logger.info("solodit_docs=%d", len(docs))
    return docs


# ── Solidity finance post-mortems (Rekt articles via Web Archive) ───────


async def _postmortems_docs(limit: int | None) -> list[dict[str, Any]]:
    """Fetch post-mortem articles for the most recent Rekt leaderboard hacks.

    Rekt article bodies are fetched via the Web Archive (a stable mirror of
    ``rekt.news/{slug}/``) and chunked per incident. Best-effort:
    unreachable articles are skipped.
    """
    limit = min(limit or 5, 10)
    try:
        entries = await _fetch_rekt_leaderboard()
    except Exception as e:
        logger.warning("postmortems_rekt_fetch_failed: %s", str(e)[:150])
        return []
    base = REKT_LEADERBOARD_URL.rsplit("/", 1)[0]
    docs: list[dict[str, Any]] = []
    fetched = 0
    for entry in entries:
        slug = str(entry.get("slug", "")).strip()
        title = str(entry.get("title", "")).strip()
        if not slug or not title:
            continue
        url = f"https://web.archive.org/web/2/{base}/{slug}/"
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as c:
                r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200:
                    continue
                text = _html_to_text(r.text)
        except Exception as e:
            logger.debug("postmortem_fetch_failed slug=%s err=%s", slug, str(e)[:100])
            continue
        if len(text) < 200:
            continue
        vector = _detect_attack_vector(text[:2000])
        chain = _detect_chain(f"{title} {text[:2000]}")
        chunks = smart_split(text)
        for ci, chunk in enumerate(chunks):
            chunk_key = f"postmortems:{slug}:{ci}" if len(chunks) > 1 else f"postmortems:{slug}"
            docs.append(
                {
                    "rel_key": chunk_key,
                    "title": f"Post-mortem: {title}",
                    "text": chunk,
                    "metadata": {
                        "source_type": "postmortems",
                        "protocol": title,
                        "attack_vector": vector,
                        "chain": chain,
                        "severity": "high",
                        "source_url": url,
                        "slug": slug,
                    },
                }
            )
        fetched += 1
        if limit and fetched >= limit:
            break
    logger.info("postmortems_docs=%d", len(docs))
    return docs


def _html_to_text(html: str) -> str:
    """Minimal HTML→text: strip scripts/styles/tags and collapse whitespace."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ── Orchestration ──────────────────────────────────────────────────────

SOURCE_TABLE: dict[str, tuple[str, str, tuple[str, ...] | None]] = {
    # name → (repo URL, local dir name, subdirs to keep)
    "defihacklabs": (
        "https://github.com/SunWeb3Sec/DeFiHackLabs.git",
        "defihacklabs",
        ("src", "past"),
    ),
    "cryptoscamdb": ("https://github.com/CryptoScamDB/blacklist.git", "blacklist", ("data",)),
    "slowmist": ("https://github.com/slowmist/Knowledge-Base.git", "slowmist", None),
    "web3hacks": (
        "https://github.com/Cyfrin/sc-exploits-minimized.git",
        "cyfrin-exploits",
        ("src",),
    ),
}


async def _collect_docs(
    source: str,
    limit: int | None,
    fetch_rekt: bool,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    if source in SOURCE_TABLE:
        repo = SOURCE_TABLE[source]
        target = KNOWLEDGE_DIR / repo[1]
        if not target.exists():
            target = _clone_repo(repo[0], repo[1], repo[2])
        elif refresh:
            git_refresh(str(target))
        if source == "defihacklabs":
            return _defihacklabs_docs(target, limit)
        if source == "cryptoscamdb":
            return _cryptoscamdb_docs(target, limit)
        if source == "slowmist":
            return _slowmist_docs(target, limit)
        if source == "web3hacks":
            return _web3hacks_docs(target, limit)
    if source == "rekt_news":
        if not fetch_rekt:
            return []
        entries = await _fetch_rekt_leaderboard()
        return _rekt_docs(entries, limit)
    if source == "swc_registry":
        return await _swc_docs(limit)
    if source == "defillama_hacks":
        return await _defillama_hacks_docs(limit)
    if source == "immunefi":
        return await _immunefi_docs(limit)
    if source == "openzeppelin_audits":
        return await _solodit_docs(limit)
    if source == "postmortems":
        return await _postmortems_docs(limit)
    raise ValueError(f"unknown source: {source}")


async def _ingest_source(
    source: str,
    client: httpx.AsyncClient,
    checkpoint: Checkpoint,
    limit: int | None,
    dry_run: bool,
    force: bool,
    fetch_rekt: bool,
    dedup: bool = True,
    dedup_threshold: float = DEDUP_THRESHOLD_DEFAULT,
    priority_only: str | None = None,
    refresh: bool = False,
) -> tuple[int, int, int, int]:
    """Ingest one source.

    Returns (upserted, skipped_by_checkpoint, total_chunks, dedup_skipped).
    """
    collection = SOURCE_COLLECTIONS[source]
    docs = await _collect_docs(source, limit, fetch_rekt, refresh=refresh)
    if not docs:
        logger.info("source=%s no docs collected", source)
        return 0, 0, 0, 0
    if priority_only:
        docs = filter_by_severity(docs, priority_only)
        logger.info("source=%s priority_only=%s kept=%d", source, priority_only, len(docs))
    # Exploit/critical content first, reference/taxonomy later.
    docs = sort_by_priority(docs)
    await _ensure_collection(client, collection)
    logger.info("source=%s docs=%d collection=%s", source, len(docs), collection)

    upserted = 0
    skipped = 0
    total_chunks = 0
    dedup_skipped = 0
    pending: list[dict[str, Any]] = []  # (rel_key, text, metadata) not yet embedded

    for doc in docs:
        text = doc["text"]
        metadata = dict(doc["metadata"])
        metadata["title"] = doc.get("title", "")
        rel_key = doc["rel_key"]
        if not force and checkpoint.is_done(source, rel_key, text):
            skipped += 1
            continue
        pending.append({"rel_key": rel_key, "text": text, "metadata": metadata})
        total_chunks += 1

    if dry_run:
        logger.info(
            "[DRY RUN] source=%s would ingest %d chunks (skipped %d)",
            source,
            len(pending),
            skipped,
        )
        return 0, skipped, len(pending), 0

    # Embed in batches of 50, upsert in batches of 50.
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        texts = [b["text"] for b in batch]
        vectors = await _embed_batch(texts)
        # Cross-reference new chunks against existing Qdrant content; chunks
        # with a top hit >= dedup_threshold are already ingested → skip.
        dups = (
            await dedup_vectors(vectors, collection, client, dedup_threshold, QDRANT_URL)
            if dedup
            else [False] * len(vectors)
        )
        points: list[dict[str, Any]] = []
        for b, vec, is_dup in zip(batch, vectors, dups, strict=False):
            if is_dup:
                dedup_skipped += 1
                checkpoint.mark(source, b["rel_key"], b["text"])  # don't re-embed next run
                continue
            if vec is None:
                logger.warning("embed_missing rel_key=%s", b["rel_key"][:60])
                continue
            points.append(
                _build_point(
                    source=source,
                    collection=collection,
                    rel_key=b["rel_key"],
                    content=b["text"],
                    metadata=b["metadata"],
                    vector=vec,
                )
            )
            checkpoint.mark(source, b["rel_key"], b["text"])
        if not points:
            checkpoint.save()
            continue
        ok = await _upsert_batch(client, collection, points)
        upserted += ok
        checkpoint.save()
        if (i // BATCH_SIZE + 1) % 5 == 0 or i + BATCH_SIZE >= len(pending):
            logger.info(
                "source=%s progress %d/%d chunks upserted=%d dedup_skipped=%d",
                source,
                min(i + BATCH_SIZE, len(pending)),
                len(pending),
                upserted,
                dedup_skipped,
            )

    # Freshness: record when this source was last updated (git commit time
    # for cloned repos, now for API sources).
    if source in SOURCE_TABLE:
        stamp = repo_last_updated(str(KNOWLEDGE_DIR / SOURCE_TABLE[source][1])) or ""
    else:
        stamp = datetime.now(UTC).isoformat()
    if stamp:
        checkpoint.set_last_updated(source, stamp)
        checkpoint.save()

    logger.info(
        "source=%s done upserted=%d skipped=%d total_chunks=%d dedup_skipped=%d last_updated=%s",
        source,
        upserted,
        skipped,
        total_chunks,
        dedup_skipped,
        stamp or "unknown",
    )
    return upserted, skipped, total_chunks, dedup_skipped


async def main_async(
    sources: list[str],
    limit: int | None,
    dry_run: bool,
    force: bool,
    fetch_rekt: bool,
    dedup: bool = True,
    dedup_threshold: float = DEDUP_THRESHOLD_DEFAULT,
    priority_only: str | None = None,
    refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    checkpoint = Checkpoint(CHECKPOINT_PATH)
    checkpoint.load()
    # One-time filesystem setup before the hot loop; blocking is fine here.
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    results: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=120.0) as client:
        for source in sources:
            try:
                upserted, skipped, total, dedup_skipped = await _ingest_source(
                    source,
                    client,
                    checkpoint,
                    limit,
                    dry_run,
                    force,
                    fetch_rekt,
                    dedup=dedup,
                    dedup_threshold=dedup_threshold,
                    priority_only=priority_only,
                    refresh=refresh,
                )
                results[source] = {
                    "upserted": upserted,
                    "skipped": skipped,
                    "chunks": total,
                    "dedup_skipped": dedup_skipped,
                    "last_updated": checkpoint.get_last_updated(source),
                }
            except Exception as e:
                logger.error("source=%s failed: %s", source, str(e)[:300])
                results[source] = {
                    "upserted": 0,
                    "skipped": 0,
                    "chunks": 0,
                    "dedup_skipped": 0,
                    "error": str(e)[:300],
                }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest scam/exploit knowledge bases into RAG")
    parser.add_argument(
        "--sources",
        default=(
            "defihacklabs,rekt_news,cryptoscamdb,slowmist,swc_registry,"
            "defillama_hacks,web3hacks,immunefi,openzeppelin_audits,postmortems"
        ),
        help="Comma-separated sources (all | defihacklabs,rekt_news,cryptoscamdb,"
        "slowmist,swc_registry,defillama_hacks,web3hacks,immunefi,"
        "openzeppelin_audits,postmortems)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max docs per source")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview counts without embedding/upsert"
    )
    parser.add_argument(
        "--force", action="store_true", help="Ignore checkpoint (re-ingest everything)"
    )
    parser.add_argument(
        "--no-rekt", action="store_true", help="Skip fetching rekt.news leaderboard (network)"
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Disable chunk deduplication against existing Qdrant content",
    )
    parser.add_argument(
        "--dedup-threshold",
        type=float,
        default=DEDUP_THRESHOLD_DEFAULT,
        help=f"Vector similarity above which a chunk is considered a duplicate "
        f"(default {DEDUP_THRESHOLD_DEFAULT})",
    )
    parser.add_argument(
        "--priority-only",
        type=str,
        default=None,
        metavar="SEVERITY",
        help="Only ingest chunks at or above SEVERITY (critical|high|medium|low|info|unknown)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="git pull cloned sources before ingesting (freshness)",
    )
    args = parser.parse_args()

    if args.sources.strip().lower() == "all":
        sources = list(SOURCE_COLLECTIONS.keys())
    else:
        sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in sources if s not in SOURCE_COLLECTIONS]
    if unknown:
        parser.error(f"unknown sources: {', '.join(unknown)}")

    results = asyncio.run(
        main_async(
            sources,
            args.limit,
            args.dry_run,
            args.force,
            not args.no_rekt,
            dedup=not args.no_dedup,
            dedup_threshold=args.dedup_threshold,
            priority_only=args.priority_only,
            refresh=args.refresh,
        )
    )

    print("=" * 70)
    print("SCAM KNOWLEDGE INGEST SUMMARY")
    print("=" * 70)
    for source, stats in results.items():
        print(
            f"  {source:<22} collection={SOURCE_COLLECTIONS[source]:<22} "
            f"upserted={stats.get('upserted', 0)} skipped={stats.get('skipped', 0)} "
            f"dedup={stats.get('dedup_skipped', 0)} chunks={stats.get('chunks', 0)}"
        )
        if stats.get("last_updated"):
            print(f"    last_updated: {stats['last_updated']}")
        if stats.get("error"):
            print(f"    ERROR: {stats['error']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
