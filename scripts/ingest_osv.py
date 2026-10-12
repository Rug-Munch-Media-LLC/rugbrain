#!/usr/bin/env python3
"""Ingest OSV.dev vulnerability database into the ``security_alerts_8b`` collection.

Source: https://api.osv.dev — open vulnerability database from Google.
Public API, no authentication required. Covers 16+ ecosystems (npm, PyPI, Go, Maven, etc.)
with CVE/GHSA identifiers, affected versions, severity, and references.

Target: security_alerts_8b (thin, 125 points, owned, 4096-dim canonical lane).
This collection is the canonical security-alerts write target with 4096-dim vectors.

Each vulnerability is embedded (4096-dim qwen3-embedding-8b via DeepInfra primary,
llama.cpp local fallback) and upserted with filterable metadata:
- osv_id, cve_ids, ghsa_ids, ecosystem, package, severity, summary, modified, published

Idempotent: point IDs are deterministic seeds (``osv:{osv_id}``) which the store
converts to UUIDv5 point IDs, so re-running overwrites the same points.

Usage:
    python scripts/ingest_osv.py --dry-run --limit 50
    python scripts/ingest_osv.py --days 30        # last 30 days only
    python scripts/ingest_osv.py --ecosystem Go   # filter by ecosystem
    python scripts/ingest_osv.py                  # full recent history

Requires (host):
  - Qdrant at QDRANT_URL (default http://127.0.0.1:6334)
  - Canonical embedder (DeepInfra primary via DEEPINFRA_API_KEY,
    local llama.cpp :8004 fallback)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Use the canonical 4096-dim embedder (deepinfra -> local fallback)
from rugbrain.embedder import get_embedder
from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_osv")

OSV_API = "https://api.osv.dev"
COLLECTION = "security_alerts_8b"
SOURCE = "osv"
SOURCE_URL = "https://osv.dev"
EMBED_DIM = 4096  # canonical qwen3-embedding-8b
REQUEST_TIMEOUT = 60.0
BATCH_SIZE = 50

# Severity mapping from OSV to RugBrain severity levels
SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "UNKNOWN": "medium",
}


def _severity_from_osv(vuln: dict[str, Any]) -> str:
    """Extract severity from OSV vulnerability entry."""
    # OSV may have database_specific severity or ecosystem_specific
    for key in ("database_specific", "ecosystem_specific"):
        spec = vuln.get(key) or {}
        if isinstance(spec, dict):
            sev = spec.get("severity")
            if sev and isinstance(sev, str):
                return SEVERITY_MAP.get(sev.upper(), "medium")
    # Fallback: derive from CVSS if available
    for affected in vuln.get("affected", []):
        for r in affected.get("ranges", []):
            if r.get("type") == "ECOSYSTEM":
                for evt in r.get("events", []):
                    if "score" in evt or "cvss" in str(evt).lower():
                        # Could parse CVSS but skip for simplicity
                        pass
    return "medium"


def _extract_cve_ghsa(vuln: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Extract CVE and GHSA IDs from vulnerability aliases."""
    cves: list[str] = []
    ghsas: list[str] = []
    for alias in vuln.get("aliases", []):
        if alias.startswith("CVE-"):
            cves.append(alias)
        elif alias.startswith("GHSA-"):
            ghsas.append(alias)
    return cves, ghsas


def _text_for_embedding(vuln: dict[str, Any]) -> str:
    """Build the text content to embed for a vulnerability."""
    parts = []
    # Title / summary
    if vuln.get("summary"):
        parts.append(f"Summary: {vuln['summary']}")
    if vuln.get("details"):
        parts.append(f"Details: {vuln['details'][:500]}")  # truncate long details
    # Identifiers
    cves, ghsas = _extract_cve_ghsa(vuln)
    if cves:
        parts.append(f"CVE IDs: {', '.join(cves)}")
    if ghsas:
        parts.append(f"GHSA IDs: {', '.join(ghsas)}")
    # Affected packages
    for aff in vuln.get("affected", []):
        pkg = aff.get("package", {})
        name = pkg.get("name", "")
        eco = pkg.get("ecosystem", "")
        if name and eco:
            versions = [v for v in aff.get("versions", []) if v][:10]
            parts.append(
                f"Affected: {eco}/{name} (versions: {', '.join(versions) if versions else 'unknown'})"
            )
    return "\n".join(parts)


def _build_metadata(vuln: dict[str, Any]) -> dict[str, Any]:
    """Build filterable metadata payload for the vulnerability."""
    cves, ghsas = _extract_cve_ghsa(vuln)
    ecosystems = set()
    packages = set()
    for aff in vuln.get("affected", []):
        pkg = aff.get("package", {})
        if pkg.get("ecosystem"):
            ecosystems.add(pkg["ecosystem"])
        if pkg.get("name"):
            packages.add(pkg["name"])

    return {
        "title": vuln.get("summary", vuln.get("id", "OSV vulnerability"))[:200],
        "osv_id": vuln.get("id", ""),
        "cve_ids": cves,
        "ghsa_ids": ghsas,
        "ecosystems": sorted(ecosystems),
        "packages": sorted(packages)[:20],
        "modified": vuln.get("modified"),
        "published": vuln.get("published"),
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "references": [ref.get("url") for ref in vuln.get("references", []) if ref.get("url")][:10],
        "schema_version": vuln.get("schema_version"),
        "knowledge_type": "vulnerability",
    }


async def fetch_vulns_by_ecosystem(
    client: httpx.AsyncClient,
    ecosystem: str,
    limit: int | None,
    days: int | None,
) -> list[dict[str, Any]]:
    """Fetch vulnerabilities for a specific ecosystem using OSV batch query."""
    # Strategy: use querybatch with popular packages in the ecosystem
    # For simplicity, we'll query a set of known packages per ecosystem
    # A more thorough approach would paginate the full OSV database.

    # Known high-value packages per ecosystem (could be expanded)
    ECOSYSTEM_PACKAGES: dict[str, list[str]] = {
        "Go": [
            "gin-gonic/gin",
            "ethereum/go-ethereum",
            "cosmos/cosmos-sdk",
            "tendermint/tendermint",
        ],
        "npm": ["ethers", "web3", "@solana/web3.js", "viem", "hardhat", "truffle"],
        "PyPI": ["web3", "eth-account", "solana", "brownie", "eth-typing", "eth-utils"],
        "Maven": ["org.web3j:core", "org.bitcoinj:bitcoinj"],
        "Rust": ["solana-sdk", "ethers-rs", "alloy"],
    }

    packages = ECOSYSTEM_PACKAGES.get(ecosystem, [])
    if not packages:
        logger.warning("osv_no_packages_for_ecosystem ecosystem=%s", ecosystem)
        return []

    # Build batch query
    queries = []
    for pkg in packages:
        if ecosystem == "Go":
            # Go packages use module path
            queries.append({"package": {"name": pkg, "ecosystem": "Go"}})
        else:
            queries.append({"package": {"name": pkg, "ecosystem": ecosystem}})

    # Execute batch query
    try:
        r = await client.post(
            f"{OSV_API}/v1/querybatch", json={"queries": queries}, timeout=REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
    except Exception as e:
        logger.warning("osv_querybatch_failed ecosystem=%s err=%s", ecosystem, str(e)[:200])
        return []

    # Flatten vulnerabilities with details
    vulns: list[dict[str, Any]] = []
    seen_ids = set()
    for i, result in enumerate(results):
        for vuln_summary in result.get("vulns", []):
            osv_id = vuln_summary.get("id")
            if not osv_id or osv_id in seen_ids:
                continue
            seen_ids.add(osv_id)
            # Fetch full vulnerability details
            try:
                vr = await client.get(f"{OSV_API}/v1/vulns/{osv_id}", timeout=REQUEST_TIMEOUT)
                vr.raise_for_status()
                full_vuln = vr.json()
                vulns.append(full_vuln)
            except Exception as e:
                logger.debug("osv_fetch_detail_failed osv_id=%s err=%s", osv_id, str(e)[:120])
                continue

    # Filter by date if requested
    if days:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        filtered = []
        for v in vulns:
            mod_str = v.get("modified") or v.get("published")
            if mod_str:
                try:
                    mod_dt = datetime.fromisoformat(mod_str.replace("Z", "+00:00"))
                    if mod_dt >= cutoff:
                        filtered.append(v)
                except Exception:
                    filtered.append(v)  # keep if parse fails
            else:
                filtered.append(v)
        vulns = filtered

    if limit:
        vulns = vulns[:limit]

    logger.info("osv_fetched ecosystem=%s total_vulns=%d", ecosystem, len(vulns))
    return vulns


async def run(
    dry_run: bool, limit: int | None, days: int | None, ecosystems: list[str] | None
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "source": SOURCE,
        "fetched": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    if ecosystems is None:
        ecosystems = ["Go", "npm", "PyPI", "Maven", "Rust"]

    all_vulns: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for eco in ecosystems:
            vulns = await fetch_vulns_by_ecosystem(client, eco, limit, days)
            all_vulns.extend(vulns)

        stats["fetched"] = len(all_vulns)
        if not all_vulns:
            logger.info("osv_no_vulns_to_ingest")
            return stats

        # Embed in batches
        embedder = get_embedder()
        texts = [_text_for_embedding(v) for v in all_vulns]
        metadatas = [_build_metadata(v) for v in all_vulns]

        vectors: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            try:
                batch_vecs = await embedder.embed(batch)
                vectors.extend(batch_vecs)
                stats["embedded"] += len([v for v in batch_vecs if v])
            except Exception as e:
                logger.warning("osv_embed_batch_failed batch_start=%d err=%s", i, str(e)[:200])
                vectors.extend([[] for _ in batch])
                stats["errors"] += len(batch)

        if dry_run:
            logger.info("dry_run would_upsert=%d into=%s", stats["embedded"], COLLECTION)
            return stats

        # Upsert to Qdrant
        store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
        for vuln, vec, meta in zip(all_vulns, vectors, metadatas, strict=False):
            if not vec:
                stats["errors"] += 1
                continue
            doc_id = f"osv:{vuln.get('id', 'unknown')}"
            ok = await store.insert(
                doc_id=doc_id,
                collection=COLLECTION,
                embedding=vec,
                content=_text_for_embedding(vuln),
                metadata=meta,
                source=SOURCE,
                severity=_severity_from_osv(vuln),
                chain="multi",
            )
            if ok:
                stats["upserted"] += 1
            else:
                stats["errors"] += 1

        await store.close()

    logger.info(
        "osv_done fetched=%d embedded=%d upserted=%d errors=%d dry_run=%s",
        stats["fetched"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
        dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="OSV.dev vulnerabilities → security_alerts_8b")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + embed but do not upsert")
    parser.add_argument("--limit", type=int, default=None, help="Max vulnerabilities to ingest")
    parser.add_argument(
        "--days", type=int, default=None, help="Only vulns modified within last N days"
    )
    parser.add_argument("--ecosystem", action="append", help="Filter by ecosystem (can repeat)")
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit, args.days, args.ecosystem))
    import json

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
