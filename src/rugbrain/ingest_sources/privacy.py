"""Privacy protocol source — Tornado Cash core contracts (archived).

Source: https://github.com/tornadocash/tornado-core — the canonical
zero-knowledge mixer implementation (``contracts/``): ``Tornado.sol``
(zk deposit/withdraw with MerkleTreeWithHistory + SNARK verifier),
``TornadoProxy.sol``, ``ETHTornado.sol``, ``ERC20Tornado.sol``.

The repo was **archived** after the 2022 OFAC sanctions; the raw content
remains fetchable and is valuable as the reference privacy-protocol
implementation. Documents are flagged ``metadata.archived: True``.

doc_type: ``privacy_protocol``. Mocks and test fixtures are excluded.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

REPO = "tornadocash/tornado-core"
REF = "master"
DEFAULT_COLLECTION = "privacy_protocol"
DOC_TYPE = "privacy_protocol"

# Skip mocks/tests — not production mixer code.
EXCLUDE_DIRS = ("Mocks", "mocks", "test", "tests", "lib", "node_modules")

_CONTRACT_RE = re.compile(r"\b(?:contract|abstract contract|interface|library)\s+(\w+)")
_PRAGMA_RE = re.compile(r"^pragma\s+solidity\s+([^;]+);", re.MULTILINE)
_LICENSE_RE = re.compile(r"^//\s*SPDX-License-Identifier:\s*(\S+)", re.MULTILINE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_tornado_file(rel_path: str, code: str) -> IngestDoc | None:
    """Normalize one Tornado Cash ``.sol`` file into a privacy_protocol doc."""
    if not code.strip():
        return None
    names = _CONTRACT_RE.findall(code)
    pragma = _PRAGMA_RE.search(code)
    license_id = _LICENSE_RE.search(code)
    title = names[0] if names else rel_path.split("/")[-1]
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    header = (
        f"Tornado Cash privacy-protocol contract code — {title}.\n"
        f"Path: {rel_path}\n"
        f"Solidity version: {pragma.group(1).strip() if pragma else 'unknown'}\n"
        f"Declarations: {', '.join(names[:8]) or 'none'}\n"
        "Note: source repo is archived (post-sanctions); reference implementation only.\n"
    )
    return IngestDoc(
        content=f"{header}\n```solidity\n{code}\n```",
        doc_id=f"tornado_cash:{rel_path}:{_content_hash(code[:4000])}",
        doc_type=DOC_TYPE,
        source="tornado_cash",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": "Tornado Cash",
            "contract_name": title,
            "contracts": names[:8],
            "pragma": pragma.group(1).strip() if pragma else "",
            "license": license_id.group(1) if license_id else "",
            "rel_path": rel_path,
            "source_url": source_url,
            "archived": True,
            "chain": "EVM",
            "ecosystem": "Privacy",
            "doc_type": DOC_TYPE,
        },
    )


class TornadoSource(Source):
    """Tornado Cash core mixer contracts (archived reference implementation)."""

    name = "tornado_cash"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Tornado Cash zk-mixer core contracts (archived; privacy protocol reference)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("tornado_no_entries (rate-limited or repo moved)")
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
            doc = parse_tornado_file(path, code)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REPO",
    "TornadoSource",
    "parse_tornado_file",
]
