#!/usr/bin/env python3
"""Ingest curated crypto security patterns into the RugBrain knowledge graph.

Part 2 of the contract-knowledge expansion. Builds the ``contract_security``
and ``audit_findings`` Qdrant collections from:

  - SECURITY_PATTERNS  — reusable Ethereum + Solana vulnerability classes,
    each with vulnerable + fixed code, detection heuristics and severity.
  - SWC fallbacks      — a short doc per SWC-100..136, completing the SWC
    Registry taxonomy inside ``contract_security`` (full entries live in
    ``threat_intel`` via scripts/ingest_swc_registry.py).
  - AUDIT_FINDINGS     — real exploit/audit case studies (Euler, Mango,
    Wormhole, Nomad, ...) for ``audit_findings``.

Each pattern is embedded (1024-dim bge-m3) and upserted with canonical
metadata (category, severity, ecosystem, swc, source). Point IDs are
deterministic UUIDv5 from content hashes → idempotent re-runs.

Usage:
    python scripts/ingest_security_patterns.py --dry-run
    python scripts/ingest_security_patterns.py --limit 10
    python scripts/ingest_security_patterns.py

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
from typing import Any

import httpx

from rugbrain.crypto_schema import SWC_SEVERITY, SWC_VECTORS
from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_security_patterns")

# scripts/ dir on sys.path so sibling data modules import cleanly.
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from contract_knowledge_data import (  # noqa: E402
    AUDIT_FINDINGS,
    SECURITY_PATTERNS,
    SWC_FALLBACK_TITLES,
)
from ingest_common import content_hash, embed_many  # noqa: E402

PATTERN_COLLECTION = "contract_security"
AUDIT_COLLECTION = "audit_findings"
SOURCE = "security_patterns"


def _pattern_doc(pattern: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    """Build (doc_id, content, metadata, severity) for one security pattern."""
    pid = pattern["pattern_id"]
    title = pattern["title"]
    ecosystem = pattern.get("ecosystem", "ethereum")
    severity = pattern.get("severity", "high")
    content = (
        f"{title} — {ecosystem} security pattern.\n"
        f"Category: {pattern.get('category', '')}. Severity: {severity}.\n"
        f"SWC: {pattern.get('swc', '') or 'n/a'}.\n"
        f"Description: {pattern.get('description', '')}\n"
        f"Vulnerable code:\n{pattern.get('vulnerable_code', '')}\n"
        f"Fixed code:\n{pattern.get('fixed_code', '')}\n"
        f"Detection heuristics: {'; '.join(pattern.get('detection_heuristics', []))}"
    )
    metadata = {
        "pattern_id": pid,
        "title": title,
        "ecosystem": ecosystem,
        "category": pattern.get("category", ""),
        "swc": pattern.get("swc", ""),
        "knowledge_type": "security_pattern",
        "vulnerable_code": pattern.get("vulnerable_code", ""),
        "fixed_code": pattern.get("fixed_code", ""),
        "detection_heuristics": pattern.get("detection_heuristics", []),
        "references": pattern.get("references", []),
        "source_url": pattern.get("references", [""])[0] if pattern.get("references") else "",
        "chain": ecosystem,
    }
    return f"secpat:{pid}:{content_hash(content)}", content, metadata, severity


def _swc_doc(swc_id: str) -> tuple[str, str, dict[str, Any], str]:
    """Build a short SWC doc completing the registry inside contract_security."""
    title = SWC_FALLBACK_TITLES.get(swc_id, swc_id)
    vector = SWC_VECTORS.get(swc_id, "smart-contract-exploit")
    severity = SWC_SEVERITY.get(swc_id, "high")
    content = (
        f"{swc_id} — {title}. Smart Contract Weakness Classification entry "
        f"(short form). Attack vector: {vector}. Severity: {severity}. "
        f"See the SWC Registry for the full description and remediation."
    )
    metadata = {
        "pattern_id": f"swc:{swc_id}",
        "title": title,
        "ecosystem": "ethereum",
        "category": vector,
        "swc": swc_id,
        "knowledge_type": "swc_entry",
        "source_url": f"https://github.com/SmartContractSecurity/SWC-registry/blob/master/entries/docs/{swc_id}.md",
        "chain": "ethereum",
    }
    return f"secpat:swc:{swc_id}", content, metadata, severity


def _audit_doc(finding: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    """Build (doc_id, content, metadata, severity) for one audit finding."""
    fid = finding["finding_id"]
    severity = finding.get("severity", "high")
    content = (
        f"{finding['title']}.\n"
        f"Pattern class: {finding.get('pattern_class', '')}.\n"
        f"Description: {finding.get('description', '')}\n"
        f"Detection heuristics: {'; '.join(finding.get('detection_heuristics', []))}\n"
        f"Remediation: {finding.get('remediation', '')}\n"
        f"Reference: {finding.get('reference_url', '')}"
    )
    metadata = {
        "finding_id": fid,
        "title": finding["title"],
        "ecosystem": finding.get("ecosystem", "ethereum"),
        "category": finding.get("category", ""),
        "pattern_class": finding.get("pattern_class", ""),
        "knowledge_type": "audit_finding",
        "reference_url": finding.get("reference_url", ""),
        "amount_lost_usd": finding.get("amount_lost_usd", 0.0),
        "remediation": finding.get("remediation", ""),
        "chain": finding.get("ecosystem", "ethereum"),
    }
    return f"audit:{fid}", content, metadata, severity


async def _ingest(
    client: httpx.AsyncClient,
    store: QdrantVectorStore,
    docs: list[tuple[str, str, dict[str, Any], str]],
    collection: str,
    dry_run: bool,
) -> int:
    if dry_run:
        logger.info("[DRY] would ingest %d points into %s", len(docs), collection)
        return len(docs)
    inserted = 0
    for i in range(0, len(docs), 8):
        batch = docs[i : i + 8]
        texts = [d[1] for d in batch]
        vectors = await embed_many(client, texts)
        for (doc_id, content, metadata, severity), vector in zip(batch, vectors, strict=False):
            if not vector:
                logger.warning("embed_empty doc=%s", doc_id[:40])
                continue
            ok = await store.insert(
                doc_id=doc_id,
                collection=collection,
                embedding=vector,
                content=content,
                metadata=metadata,
                source=SOURCE,
                severity=severity,
                chain=str(metadata.get("chain", "")),
            )
            if ok:
                inserted += 1
    logger.info("collection=%s inserted=%d", collection, inserted)
    return inserted


async def main_async(args: argparse.Namespace) -> dict[str, int]:
    docs: dict[str, list[tuple[str, str, dict[str, Any], str]]] = {
        PATTERN_COLLECTION: [_pattern_doc(p) for p in SECURITY_PATTERNS],
        AUDIT_COLLECTION: [_audit_doc(f) for f in AUDIT_FINDINGS],
    }
    # Complete SWC coverage inside contract_security.
    docs[PATTERN_COLLECTION] += [_swc_doc(s) for s in sorted(SWC_FALLBACK_TITLES)]
    if args.limit:
        docs[PATTERN_COLLECTION] = docs[PATTERN_COLLECTION][: args.limit]
        docs[AUDIT_COLLECTION] = docs[AUDIT_COLLECTION][: args.limit]

    totals: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        store = QdrantVectorStore()
        try:
            for collection, collection_docs in docs.items():
                totals[collection] = await _ingest(
                    client, store, collection_docs, collection, args.dry_run
                )
        finally:
            await store.close()
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest security patterns + audit findings into contract_security / audit_findings"
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max docs per collection")
    args = parser.parse_args()

    totals = asyncio.run(main_async(args))
    print("=" * 60)
    print("SECURITY PATTERN INGEST COMPLETE")
    for col, n in totals.items():
        print(f"  {col}: {n}")
    print("=" * 60)


if __name__ == "__main__":
    main()
