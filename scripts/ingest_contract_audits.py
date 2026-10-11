#!/usr/bin/env python3
"""Ingest PUBLIC contract audit write-ups into the RAG contract_audits collection.

Sources (all public, legally ingestible):
  1. Trail of Bits publications (already partially ingested) — https://github.com/trailofbits/publications
  2. OpenZeppelin audits — https://github.com/OpenZeppelin/security-audits
  3. ConsenSys Diligence audits — https://github.com/ConsenSys/smart-contract-audits
  4. Quantstamp audits — https://github.com/quantstamp/audits
  5. CertiK audits — public reports on their site
  6. Hacken audits — public reports
  7. PeckShield audits — https://github.com/peckshield/publications
  8. SlowMist open reports — already partially ingested
  9. Trail of Bits blog posts — https://blog.trailofbits.com/
  10. Neodyme audit reports — https://github.com/neodyme/audits

Strategy: Focus on GitHub repositories that publish audit reports as markdown/PDF.
Fetch repository contents, filter for audit reports, extract structured data.

Each audit finding is embedded (2560-dim qwen3-embedding-4b) and upserted into
contract_audits with filterable metadata: protocol, issue, severity, audit_team,
source_url, date, vulnerability_class, chain.

Idempotent: point IDs are deterministic based on source, path, and content hash.

Usage:
    python scripts/ingest_contract_audits.py --dry-run
    python scripts/ingest_contract_audits.py --limit 50
    python scripts/ingest_contract_audits.py --source openzeppelin
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ingest_common2 import build_doc, write_docs

from rugbrain.qdrant_store import QdrantVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_contract_audits")

COLLECTION = "contract_audits"
REQUEST_TIMEOUT = 30.0
USER_AGENT = "RugMunch RAG research agent; admin@rugmunch.io"

# GitHub API base
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

# Audit source repositories
AUDIT_SOURCES: dict[str, dict[str, Any]] = {
    "openzeppelin": {
        "repo": "OpenZeppelin/security-audits",
        "branch": "master",
        "paths": ["audits"],
        "description": "OpenZeppelin security audits",
    },
    "consensys": {
        "repo": "ConsenSys/smart-contract-audits",
        "branch": "master",
        "paths": ["audits"],
        "description": "ConsenSys Diligence audit reports",
    },
    "quantstamp": {
        "repo": "quantstamp/audits",
        "branch": "main",
        "paths": ["audits"],
        "description": "Quantstamp audit reports",
    },
    "peckshield": {
        "repo": "peckshield/publications",
        "branch": "master",
        "paths": ["audits", "reports"],
        "description": "PeckShield audit publications",
    },
    "neodyme": {
        "repo": "neodyme/audits",
        "branch": "main",
        "paths": ["."],
        "description": "Neodyme audit reports",
    },
    "trailofbits_blog": {
        "repo": "trailofbits/publications",
        "branch": "master",
        "paths": ["datasets/smart_contract_audit_findings"],
        "description": "Trail of Bits audit finding datasets",
    },
}

# Keywords to identify audit report files
_AUDIT_EXTENSIONS = (".md", ".markdown", ".pdf", ".txt", ".csv")
_AUDIT_FILENAME_PATTERNS = [
    r"audit",
    r"report",
    r"finding",
    r"vulnerab",
    r"security",
    r"assessment",
]


async def _github_api_request(client: httpx.AsyncClient, endpoint: str, headers: dict | None = None) -> Any:
    """Make a GitHub API request with optional auth."""
    h = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github.v3+json"}
    if headers:
        h.update(headers)
    r = await client.get(f"{GITHUB_API}{endpoint}", headers=h, timeout=REQUEST_TIMEOUT)
    if r.status_code == 403:
        logger.warning("github_rate_limited endpoint=%s", endpoint)
        return None
    if r.status_code == 404:
        logger.warning("github_not_found endpoint=%s", endpoint)
        return None
    r.raise_for_status()
    return r.json()


async def _fetch_repo_tree(client: httpx.AsyncClient, repo: str, branch: str, path: str = "") -> list[dict[str, Any]]:
    """Fetch repository tree recursively."""
    endpoint = f"/repos/{repo}/git/trees/{branch}"
    if path:
        endpoint += f"?recursive=1"
    data = await _github_api_request(client, endpoint)
    if not data:
        return []
    return data.get("tree", [])


async def _fetch_file_content(client: httpx.AsyncClient, repo: str, branch: str, path: str) -> str | None:
    """Fetch raw file content from GitHub."""
    url = f"{GITHUB_RAW}/{repo}/{branch}/{path}"
    try:
        r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        logger.warning("fetch_file_failed repo=%s path=%s err=%s", repo, path, str(e)[:100])
    return None


def _is_audit_file(filename: str) -> bool:
    """Check if a file is likely an audit report."""
    lower = filename.lower()
    if not any(lower.endswith(ext) for ext in _AUDIT_EXTENSIONS):
        return False
    return any(re.search(pat, lower) for pat in _AUDIT_FILENAME_PATTERNS)


def _extract_audit_metadata(content: str, filename: str, source: str) -> dict[str, Any]:
    """Extract structured metadata from audit report content."""
    metadata = {
        "source": source,
        "filename": filename,
        "protocol": "unknown",
        "issue": "unknown",
        "severity": "medium",
        "audit_team": source,
        "vulnerability_class": "unknown",
        "chain": "ethereum",
        "date": "",
    }
    
    # Try to extract protocol name from filename or content
    # Remove common prefixes/suffixes
    name = re.sub(r"\.(md|markdown|pdf|txt|csv)$", "", filename)
    name = re.sub(r"^(audit[-_]?|report[-_]?|security[-_]?|finding[-_]?)+", "", name, flags=re.I)
    name = re.sub(r"[-_](audit|report|security|finding|vulnerability)$", "", name, flags=re.I)
    
    if name:
        metadata["protocol"] = name.replace("-", " ").replace("_", " ").title()
    
    # Extract severity from content
    content_lower = content.lower()
    if "critical" in content_lower:
        metadata["severity"] = "critical"
    elif "high" in content_lower and "high severity" in content_lower:
        metadata["severity"] = "high"
    elif "medium" in content_lower:
        metadata["severity"] = "medium"
    elif "low" in content_lower:
        metadata["severity"] = "low"
    
    # Try to extract vulnerability class
    vuln_keywords = {
        "reentrancy": "reentrancy",
        "overflow": "arithmetic",
        "underflow": "arithmetic",
        "access control": "access_control",
        "authorization": "access_control",
        "unchecked": "unchecked_return",
        "race condition": "race_condition",
        "front.run": "front_running",
        "flash.loan": "flash_loan",
        "oracle": "oracle_manipulation",
        "governance": "governance",
        "denial": "dos",
        "gas": "gas_optimization",
    }
    for keyword, vuln_class in vuln_keywords.items():
        if keyword in content_lower:
            metadata["vulnerability_class"] = vuln_class
            break
    
    # Try to extract date from content
    date_patterns = [
        r"(\d{4}-\d{2}-\d{2})",
        r"(\d{1,2}/\d{1,2}/\d{4})",
        r"(\w+ \d{1,2}, \d{4})",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, content)
        if match:
            metadata["date"] = match.group(1)
            break
    
    return metadata


def _split_audit_findings(content: str, max_chunk_size: int = 2000) -> list[str]:
    """Split audit report into logical chunks (by finding/section)."""
    # Try to split by common finding markers
    chunks = []
    
    # Split by common section headers
    sections = re.split(r"\n#{1,3}\s+", content)
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= max_chunk_size:
            chunks.append(section)
        else:
            # Further split long sections
            words = section.split()
            current = []
            current_len = 0
            for word in words:
                if current_len + len(word) + 1 > max_chunk_size and current:
                    chunks.append(" ".join(current))
                    current = [word]
                    current_len = len(word)
                else:
                    current.append(word)
                    current_len += len(word) + 1
            if current:
                chunks.append(" ".join(current))
    
    return chunks if chunks else [content[:max_chunk_size]]


async def _process_source(
    client: httpx.AsyncClient,
    source_name: str,
    source_config: dict[str, Any],
    limit: int | None,
) -> list[dict[str, Any]]:
    """Process a single audit source repository."""
    docs: list[dict[str, Any]] = []
    repo = source_config["repo"]
    branch = source_config["branch"]
    paths = source_config["paths"]
    
    logger.info("processing_source source=%s repo=%s", source_name, repo)
    
    try:
        tree = await _fetch_repo_tree(client, repo, branch)
        
        file_count = 0
        for entry in tree:
            if entry.get("type") != "blob":
                continue
            path = entry.get("path", "")
            
            # Filter to relevant paths
            if paths != ["."]:
                if not any(path.startswith(p.rstrip("/") + "/") or path == p for p in paths):
                    continue
            
            if not _is_audit_file(path):
                continue
            
            if limit and file_count >= limit:
                break
            
            content = await _fetch_file_content(client, repo, branch, path)
            if not content:
                continue
            
            metadata = _extract_audit_metadata(content, path, source_name)
            metadata["source_url"] = f"https://github.com/{repo}/blob/{branch}/{path}"
            
            # Split into chunks
            chunks = _split_audit_findings(content)
            
            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                rel_key = f"{source_name}:{path}:chunk-{i}"
                doc_text = (
                    f"Protocol: {metadata['protocol']}\n"
                    f"Audit Team: {metadata['audit_team']}\n"
                    f"Severity: {metadata['severity']}\n"
                    f"Vulnerability Class: {metadata['vulnerability_class']}\n"
                    f"Date: {metadata['date'] or 'unknown'}\n"
                    f"Source: {metadata['source_url']}\n"
                    f"Finding: {chunk}"
                )
                docs.append(
                    build_doc(
                        source=f"audit_{source_name}",
                        rel_key=rel_key,
                        content=doc_text,
                        metadata=metadata,
                        severity=metadata["severity"],
                        chain=metadata["chain"],
                        collection=COLLECTION,
                    )
                )
            
            file_count += 1
        
        logger.info("source_done source=%s files=%d chunks=%d", source_name, file_count, len(docs))
        
    except Exception as e:
        logger.warning("source_failed source=%s err=%s", source_name, str(e)[:200])
    
    return docs


async def run(
    dry_run: bool,
    limit: int | None,
    sources: list[str] | None,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "total_docs": 0,
        "inserted": 0,
        "chunks": 0,
        "failed": 0,
        "errors": 0,
        "dry_run": dry_run,
    }
    
    source_list = sources if sources else list(AUDIT_SOURCES.keys())
    
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        all_docs: list[dict[str, Any]] = []
        
        for source_name in source_list:
            if source_name not in AUDIT_SOURCES:
                logger.warning("unknown_source name=%s available=%s", source_name, list(AUDIT_SOURCES.keys()))
                continue
            
            source_config = AUDIT_SOURCES[source_name]
            docs = await _process_source(client, source_name, source_config, limit)
            all_docs.extend(docs)
            stats[f"{source_name}_chunks"] = len(docs)
        
        stats["total_docs"] = len(all_docs)
        
        if not all_docs:
            logger.info("contract_audits_no_docs_to_ingest")
            return stats
        
        if dry_run:
            logger.info("dry_run would_upsert=%d into=%s", len(all_docs), COLLECTION)
            return stats
        
        # Embed and upsert
        store = QdrantVectorStore()
        result = await write_docs(
            client,
            store,
            all_docs,
            source="contract_audits_ingest",
            collection=COLLECTION,
            dry_run=False,
            chunk_size=2000,
            chunk_overlap=100,
        )
        
        stats["inserted"] = result.get("inserted", 0)
        stats["chunks"] = result.get("chunks", 0)
        stats["failed"] = result.get("failed", 0)
    
    logger.info(
        "contract_audits_done inserted=%d chunks=%d failed=%d dry_run=%s",
        stats["inserted"], stats["chunks"], stats["failed"], dry_run,
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Public contract audits → Qdrant")
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max files per source")
    parser.add_argument(
        "--source", action="append", default=None, help="Restrict to a source (repeatable)"
    )
    parser.add_argument("--list", action="store_true", help="List available audit sources")
    args = parser.parse_args()
    
    if args.list:
        print("available contract audit sources:")
        for name, config in AUDIT_SOURCES.items():
            print(f"  - {name}: {config['repo']} ({config['description']})")
        return

    stats = asyncio.run(run(args.dry_run, args.limit, args.source))
    import json
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
