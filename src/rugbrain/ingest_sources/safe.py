"""Safe smart account source — audited account-abstraction contract code.

Source: https://github.com/safe-global/safe-smart-account — moved to
``safe-fndn/safe-smart-account`` (GitHub 301s the old path; we use the new
canonical repo). The Safe (formerly Gnosis Safe) smart-account contracts
(``contracts/Safe.sol``, ``SafeL2.sol``, ``base/*``, ``libraries/*``,
``modules/*``) are the reference implementation of multisig wallet
abstraction: threshold/owners, module + guard hooks, EIP-1271 signatures.

doc_type: ``contract_code`` — same semantic type as ``openzeppelin`` so the
code-aware chunking/search path handles it identically. Mocks, harnesses and
test fixtures are excluded.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

REPO = "safe-fndn/safe-smart-account"
REF = "main"
DEFAULT_COLLECTION = "contract_security"
DOC_TYPE = "contract_code"

# Skip harnesses/tests/examples — not production contract code.
EXCLUDE_DIRS = (
    "test",
    "tests",
    "mock",
    "mocks",
    "lib",
    "node_modules",
    "hardhat",
    "certora",
    "examples",
)

_CONTRACT_RE = re.compile(r"\b(?:contract|abstract contract|interface|library)\s+(\w+)")
_PRAGMA_RE = re.compile(r"^pragma\s+solidity\s+([^;]+);", re.MULTILINE)
_LICENSE_RE = re.compile(r"^//\s*SPDX-License-Identifier:\s*(\S+)", re.MULTILINE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_safe_file(rel_path: str, code: str) -> IngestDoc | None:
    """Normalize one Safe ``.sol`` file into a contract_code doc."""
    if not code.strip():
        return None
    names = _CONTRACT_RE.findall(code)
    pragma = _PRAGMA_RE.search(code)
    license_id = _LICENSE_RE.search(code)
    title = names[0] if names else rel_path.split("/")[-1]
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    header = (
        f"Safe smart-account contract code — {title}.\n"
        f"Path: {rel_path}\n"
        f"Solidity version: {pragma.group(1).strip() if pragma else 'unknown'}\n"
        f"Declarations: {', '.join(names[:8]) or 'none'}\n"
    )
    return IngestDoc(
        content=f"{header}\n```solidity\n{code}\n```",
        doc_id=f"safe_contracts:{rel_path}:{_content_hash(code[:4000])}",
        doc_type=DOC_TYPE,
        source="safe_contracts",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": "Safe",
            "contract_name": title,
            "contracts": names[:8],
            "pragma": pragma.group(1).strip() if pragma else "",
            "license": license_id.group(1) if license_id else "",
            "rel_path": rel_path,
            "source_url": source_url,
            "chain": "EVM",
            "ecosystem": "EVM",
            "doc_type": DOC_TYPE,
        },
    )


class SafeSource(Source):
    """Safe (Gnosis) smart-account multisig contract library."""

    name = "safe_contracts"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = (
        "Safe smart-account multisig contract implementations (threshold, modules, guards)"
    )
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("safe_no_entries (rate-limited or repo moved)")
            return []
        sol_entries = max_size(
            pick_files(
                entries,
                suffixes=(".sol",),
                include=("contracts/",),
                exclude_dirs=EXCLUDE_DIRS,
            ),
            limit,
        )
        docs: list[IngestDoc] = []
        for e in sol_entries:
            path = str(e.get("path", ""))
            code = await fetch_raw(client, REPO, REF, path)
            doc = parse_safe_file(path, code)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REPO",
    "SafeSource",
    "parse_safe_file",
]
