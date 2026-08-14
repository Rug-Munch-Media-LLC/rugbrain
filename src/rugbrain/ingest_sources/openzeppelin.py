"""OpenZeppelin Contracts source — audited Solidity contract code.

Source: https://github.com/OpenZeppelin/openzeppelin-contracts
``contracts/**/*.sol`` — the canonical, audited implementations of ERC
standards (ERC20, ERC721, ERC1155), access control (Ownable, AccessControl),
security utilities (ReentrancyGuard, Pausable) and more.

doc_type: ``contract_code`` — matches the code-chunk bias in
``rugbrain.search.engine`` (``contract_code`` is recognized as code-carrying).
Mocks and test fixtures are excluded: they add retrieval noise without
security value.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

REPO = "OpenZeppelin/openzeppelin-contracts"
REF = "master"
DEFAULT_COLLECTION = "contract_security"
DOC_TYPE = "contract_code"

# Skip generated/test/utility noise. Mocks are huge and not production code.
EXCLUDE_DIRS = ("contracts/mocks", "contracts/test", "lib", "test", "hardhat", "docs")

_CONTRACT_RE = re.compile(r"\b(?:contract|abstract contract|interface|library)\s+(\w+)")
_PRAGMA_RE = re.compile(r"^pragma\s+solidity\s+([^;]+);", re.MULTILINE)
_LICENSE_RE = re.compile(r"^//\s*SPDX-License-Identifier:\s*(\S+)", re.MULTILINE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_contract_file(rel_path: str, code: str) -> IngestDoc | None:
    """Normalize one OpenZeppelin ``.sol`` file into a contract_code doc."""
    if not code.strip():
        return None
    names = _CONTRACT_RE.findall(code)
    pragma = _PRAGMA_RE.search(code)
    license_id = _LICENSE_RE.search(code)
    title = names[0] if names else rel_path.split("/")[-1]
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    header = (
        f"OpenZeppelin contract code — {title}.\n"
        f"Path: {rel_path}\n"
        f"Solidity version: {pragma.group(1).strip() if pragma else 'unknown'}\n"
        f"Declarations: {', '.join(names[:8]) or 'none'}\n"
    )
    return IngestDoc(
        content=f"{header}\n```solidity\n{code}\n```",
        doc_id=f"openzeppelin:{rel_path}:{_content_hash(code[:4000])}",
        doc_type=DOC_TYPE,
        source="openzeppelin_contracts",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
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


class OpenZeppelinSource(Source):
    """GitHub OpenZeppelin audited contract library."""

    name = "openzeppelin_contracts"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Audited Solidity contract implementations (ERC standards, security utils)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, REPO, REF)
        sol_entries = pick_files(
            entries,
            suffixes=(".sol",),
            include=("contracts/",),
            exclude_dirs=EXCLUDE_DIRS,
        )
        sol_entries = max_size(sol_entries, limit)
        docs: list[IngestDoc] = []
        for e in sol_entries:
            path = str(e.get("path", ""))
            code = await fetch_raw(client, REPO, REF, path)
            doc = parse_contract_file(path, code)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REPO",
    "OpenZeppelinSource",
    "parse_contract_file",
]
