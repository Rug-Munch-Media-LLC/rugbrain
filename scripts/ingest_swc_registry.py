#!/usr/bin/env python3
"""Ingest the SWC Registry (Smart Contract Weakness Classification) into the
RAG ``crypto_vulnerabilities`` collection.

Source: https://github.com/SmartContractSecurity/SWC-registry (37 SWC entries)

Each SWC entry (a ``SWC-<id>.md`` file) classifies a smart-contract weakness with a
title, description, remediation guidance, CWE relationships and Solidity
sample code. These weakness definitions are the *canonical taxonomy* for EVM
exploits — combining them with DeFiHackLabs PoCs (real attacks) and Rekt News
(historical hacks) gives the RAG three complementary layers of crypto
security knowledge:

    SWC Registry   → "what weaknesses exist and how to spot them"
    DeFiHackLabs   → "how real exploits reproduce"
    Rekt News      → "what actually happened and how much was lost"

For each entry we:
  1. Parse the markdown: title, description, remediation, solidity samples,
     CWE references.
  2. Map the SWC ID to a canonical ``attack_vector`` and default ``severity``
     (see ``rugbrain.crypto_schema.SWC_VECTORS`` / ``SWC_SEVERITY``).
  3. Build a structured weakness document and embed with qwen3-embedding-4b (2560-dim,
     /embeddings).
  4. Upsert into ``crypto_vulnerabilities`` with ``source=swc_registry`` and canonical
     metadata (attack_vector, severity, chain=ethereum, protocol=SWC ID,
     source_type=swc_registry).

Idempotent: point IDs are deterministic UUIDv5 derived from the content hash,
so re-running overwrites the same points.

Usage:
    python scripts/ingest_swc_registry.py --dry-run
    python scripts/ingest_swc_registry.py --limit 10
    python scripts/ingest_swc_registry.py

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - at QWEN_EMBED_URL (default http://127.0.0.1:8004/v1) with qwen3-embedding-4b loaded
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
from typing import Any

import httpx

from rugbrain.crypto_schema import SWC_SEVERITY, SWC_VECTORS
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_swc")

SWC_ENTRIES_URL = (
    "https://api.github.com/repos/SmartContractSecurity/SWC-registry/contents/entries/docs"
)
SWC_RAW_BASE = (
    "https://raw.githubusercontent.com/SmartContractSecurity/SWC-registry/master/entries/docs"
)
COLLECTION = "crypto_vulnerabilities"
EMBEDDING_DIM = 2560
SOURCE = "swc_registry"
REQUEST_TIMEOUT = 30
CHUNK_SIZE = 1500
CHUNK_OVERLAP = 100

# SWC entries known to exist in the registry (fallback list when the GitHub
# API is rate-limited; fetched live otherwise).
FALLBACK_SWC_IDS = [f"SWC-{i}" for i in range(100, 137)]


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split long descriptions into windows (SWC docs are usually short)."""
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
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _section(markdown: str, heading: str) -> str:
    """Extract the text under a ``## heading`` section (until next ``## ``)."""
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
    )
    m = pattern.search(markdown)
    if not m:
        return ""
    return m.group(1).strip()


def _extract_title(markdown: str) -> str:
    """Extract the weakness title (line after the ``# Title`` marker)."""
    m = re.search(r"^# Title\s*\n+\s*([^\n]+)", markdown, re.MULTILINE)
    if m:
        return str(m.group(1)).strip()
    # Fallback: first ``# `` heading that is not the deprecation notice.
    for heading in re.findall(r"^# (.+)$", markdown, re.MULTILINE):
        if "no longer actively maintained" not in heading.lower() and heading.lower() != "title":
            return str(heading).strip()
    return ""


def _extract_solidity_samples(markdown: str) -> list[str]:
    """Extract all ```solidity code blocks (deduplicated, capped)."""
    samples: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"```solidity\s*\n(.*?)```", markdown, re.DOTALL):
        code = m.group(1).strip()
        key = _content_hash(code[:200])
        if code and key not in seen:
            seen.add(key)
            samples.append(code)
    return samples[:3]


def _extract_cwe(markdown: str) -> list[str]:
    """Extract CWE references like ``CWE-682`` from the Relationships section."""
    rel = _section(markdown, "Relationships")
    return re.findall(r"CWE-\d+", rel)


async def fetch_entry_list(client: httpx.AsyncClient) -> list[str]:
    """List SWC-<id>.md filenames via the GitHub contents API."""
    try:
        r = await client.get(SWC_ENTRIES_URL)
        if r.status_code != 200:
            logger.warning("github_list_failed status=%d — using fallback IDs", r.status_code)
            return FALLBACK_SWC_IDS
        names = [item.get("name", "") for item in r.json() if item.get("name", "").endswith(".md")]
        names.sort()
        return names
    except Exception as e:
        logger.warning("github_list_error err=%s — using fallback IDs", str(e)[:120])
        return FALLBACK_SWC_IDS


async def fetch_entry(client: httpx.AsyncClient, filename: str) -> tuple[str, str]:
    """Fetch one SWC markdown entry; returns (swc_id, markdown)."""
    url = f"{SWC_RAW_BASE}/{filename}"
    r = await client.get(url)
    r.raise_for_status()
    swc_id = filename.replace(".md", "").upper()
    return swc_id, r.text


def swc_to_doc(swc_id: str, markdown: str) -> dict[str, Any] | None:
    """Transform one SWC markdown entry into a structured weakness doc."""
    title = _extract_title(markdown) or swc_id
    description = _section(markdown, "Description")
    remediation = _section(markdown, "Remediation")
    samples = _extract_solidity_samples(markdown)
    cwe = _extract_cwe(markdown)
    if not description and not samples:
        logger.warning("swc_empty_entry swc=%s", swc_id)
        return None

    vector = SWC_VECTORS.get(swc_id, "smart-contract-exploit")
    severity = SWC_SEVERITY.get(swc_id, "high")
    text_parts = [
        f"{swc_id} — {title}. Smart Contract Weakness Classification entry.",
        f"Attack vector: {vector}. Severity: {severity}.",
    ]
    if cwe:
        text_parts.append(f"CWE relationships: {', '.join(cwe)}.")
    if description:
        text_parts.append(f"Description: {description}")
    if remediation:
        text_parts.append(f"Remediation: {remediation}")
    text = " ".join(text_parts)

    return {
        "swc_id": swc_id,
        "title": title,
        "description": description,
        "remediation": remediation,
        "samples": samples,
        "cwe": cwe,
        "attack_vector": vector,
        "severity": severity,
        "text": text,
        "url": f"https://github.com/SmartContractSecurity/SWC-registry/blob/master/entries/docs/{swc_id}.md",
    }


async def ingest_docs(docs: list[dict[str, Any]], dry_run: bool) -> int:
    """Embed + upsert SWC weakness docs into ``crypto_vulnerabilities``."""
    if dry_run:
        logger.info("[DRY RUN] would ingest %d SWC entries into %s", len(docs), COLLECTION)
        return len(docs)

    # Provider-aware: DeepInfra when keyed (bulk lane), local fallback.
    # Do NOT pin the local endpoint — `_url=` was an invalid kwarg that
    # crashed this script, and pinned URLs force bulk onto the local server
    # (both fixed 2026-09-14).
    embedder = Qwen3Embedder()
    store = QdrantVectorStore()
    inserted = failed = 0
    try:
        for i, doc in enumerate(docs, start=1):
            for ci, chunk in enumerate(_chunk_text(doc["text"])):
                doc_id = f"swc:{doc['swc_id']}:{_content_hash(chunk)}"
                metadata = {
                    "swc_id": doc["swc_id"],
                    "protocol": doc["swc_id"],
                    "title": doc["title"],
                    "category": "smart-contract",
                    "attack_vector": doc["attack_vector"],
                    "severity": doc["severity"],
                    "chain": "ethereum",
                    "date": "",
                    "amount_lost_usd": 0.0,
                    "source": SOURCE,
                    "source_type": SOURCE,
                    "source_url": doc["url"],
                    "cwe": doc["cwe"],
                    "has_sample_code": bool(doc["samples"]),
                    "chunk_index": ci,
                }
                try:
                    embedding = await embedder.embed_one(chunk[:8000])
                except Exception as e:
                    logger.warning("embed_failed swc=%s err=%s", doc["swc_id"], str(e)[:120])
                    failed += 1
                    continue
                if len(embedding) != EMBEDDING_DIM:
                    logger.warning("bad_dim swc=%s dim=%d", doc["swc_id"], len(embedding))
                    failed += 1
                    continue
                ok = await store.insert(
                    doc_id=doc_id,
                    collection=COLLECTION,
                    embedding=embedding,
                    content=chunk,
                    metadata=metadata,
                    source=SOURCE,
                    severity=doc["severity"],
                    chain="ethereum",
                )
                if ok:
                    inserted += 1
                else:
                    failed += 1
            if i % 10 == 0 or i == len(docs):
                logger.info(
                    "progress %d/%d SWC entries, %d points upserted", i, len(docs), inserted
                )
    finally:
        await store.close()
    logger.info("done swc_entries=%d inserted=%d failed=%d", len(docs), inserted, failed)
    return inserted


async def main_async(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        filenames = await fetch_entry_list(client)
        if args.limit:
            filenames = filenames[: args.limit]
        docs: list[dict[str, Any]] = []
        for filename in filenames:
            try:
                swc_id, markdown = await fetch_entry(client, filename)
            except Exception as e:
                logger.warning("fetch_failed file=%s err=%s", filename, str(e)[:120])
                continue
            doc = swc_to_doc(swc_id, markdown)
            if doc:
                docs.append(doc)
    logger.info("swc_entries_fetched=%d", len(docs))
    return await ingest_docs(docs, args.dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest SWC Registry weakness definitions into crypto_vulnerabilities"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max SWC entries to ingest")
    args = parser.parse_args()

    inserted = asyncio.run(main_async(args))
    print("=" * 60)
    print(f"SWC REGISTRY INGEST COMPLETE: {inserted} points in {COLLECTION}")
    print("=" * 60)


if __name__ == "__main__":
    main()
