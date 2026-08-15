"""Cross-chain bridge sources — LayerZero, Axelar, Chainlink CCIP.

Source repos (all EVM Solidity):

  * ``LayerZero-Labs/LayerZero-v2``      — the omnichain messaging protocol
    (OApp/OFT framework, ``packages/layerzero-v2/evm/**/contracts/*.sol``).
  * ``axelarnetwork/axelar-cgp-solidity`` — Axelar cross-chain gateway +
    gas-service contracts (``contracts/*.sol``).
  * ``smartcontractkit/ccip``            — Chainlink CCIP router/onramp/offramp
    + token pools (``contracts/src/v0.8/ccip/**/*.sol``). Repo is archived
    (read-only) but raw content remains fetchable.

All three normalize to doc_type ``bridge_architecture`` with the same
contract-code shape used by ``openzeppelin`` (header + fenced Solidity), so
downstream code-aware chunking treats them as code docs.

Notes:

  * CCIP is archived — flagged in metadata (``archived: True``).
  * Repos use different default branches; each source hardcodes its REF.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DOC_TYPE = "bridge_architecture"
DEFAULT_COLLECTION = "bridge_architecture"

LAYERZERO_REPO = "LayerZero-Labs/LayerZero-v2"
LAYERZERO_REF = "main"
AXELAR_REPO = "axelarnetwork/axelar-cgp-solidity"
AXELAR_REF = "main"
CCIP_REPO = "smartcontractkit/ccip"
CCIP_REF = "ccip-develop"

# Skip generated/test noise (mirrors openzeppelin.py excludes).
EXCLUDE_DIRS = ("test", "tests", "mock", "mocks", "lib", "node_modules", "hardhat")

_CONTRACT_RE = re.compile(r"\b(?:contract|abstract contract|interface|library)\s+(\w+)")
_PRAGMA_RE = re.compile(r"^pragma\s+solidity\s+([^;]+);", re.MULTILINE)
_LICENSE_RE = re.compile(r"^//\s*SPDX-License-Identifier:\s*(\S+)", re.MULTILINE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def bridge_contract_to_doc(
    *,
    protocol: str,
    source: str,
    repo: str,
    ref: str,
    rel_path: str,
    code: str,
    collection: str = DEFAULT_COLLECTION,
    archived: bool = False,
) -> IngestDoc | None:
    """Normalize one bridge ``.sol`` file into a ``bridge_architecture`` doc.

    Pure function (testable without network). ``protocol`` is the short
    display name (e.g. ``LayerZero``) used in the header + metadata.
    """
    if not code.strip():
        return None
    names = _CONTRACT_RE.findall(code)
    pragma = _PRAGMA_RE.search(code)
    license_id = _LICENSE_RE.search(code)
    title = names[0] if names else rel_path.split("/")[-1]
    source_url = f"https://github.com/{repo}/blob/{ref}/{rel_path}"
    archived_note = "Note: source repo is archived (read-only).\n" if archived else ""
    header = (
        f"{protocol} bridge architecture contract code — {title}.\n"
        f"Path: {rel_path}\n"
        f"Solidity version: {pragma.group(1).strip() if pragma else 'unknown'}\n"
        f"Declarations: {', '.join(names[:8]) or 'none'}\n"
        f"{archived_note}"
    )
    return IngestDoc(
        content=f"{header}\n```solidity\n{code}\n```",
        doc_id=f"{source}:{rel_path}:{_content_hash(code[:4000])}",
        doc_type=DOC_TYPE,
        source=source,
        title=title,
        url=source_url,
        metadata={
            "collection": collection,
            "protocol": protocol,
            "contract_name": title,
            "contracts": names[:8],
            "pragma": pragma.group(1).strip() if pragma else "",
            "license": license_id.group(1) if license_id else "",
            "rel_path": rel_path,
            "source_url": source_url,
            "archived": archived,
            "chain": "EVM",
            "ecosystem": "Cross-Chain",
            "doc_type": DOC_TYPE,
        },
    )


async def _fetch_bridge_repo(
    client: httpx.AsyncClient,
    *,
    protocol: str,
    source: str,
    repo: str,
    ref: str,
    include: tuple[str, ...],
    limit: int | None,
    archived: bool = False,
) -> list[IngestDoc]:
    entries = await list_repo_files(client, repo, ref)
    if not entries:
        log.warning("%s_no_entries (rate-limited or repo moved)", source)
        return []
    sol_entries = max_size(
        pick_files(
            entries,
            suffixes=(".sol",),
            include=include,
            exclude_dirs=EXCLUDE_DIRS,
        ),
        limit,
    )
    docs: list[IngestDoc] = []
    for e in sol_entries:
        path = str(e.get("path", ""))
        code = await fetch_raw(client, repo, ref, path)
        doc = bridge_contract_to_doc(
            protocol=protocol,
            source=source,
            repo=repo,
            ref=ref,
            rel_path=path,
            code=code,
            archived=archived,
        )
        if doc:
            docs.append(doc)
        if limit and len(docs) >= limit:
            break
    return docs


class LayerZeroSource(Source):
    """LayerZero v2 omnichain messaging protocol contracts."""

    name = "layerzero"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "LayerZero v2 omnichain messaging/OApp contracts (bridge architecture)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        return await _fetch_bridge_repo(
            client,
            protocol="LayerZero",
            source=self.name,
            repo=LAYERZERO_REPO,
            ref=LAYERZERO_REF,
            include=("packages/layerzero-v2/evm/",),
            limit=limit,
        )


class AxelarSource(Source):
    """Axelar cross-chain gateway + gas service contracts."""

    name = "axelar"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Axelar cross-chain gateway/gas-service contracts (bridge architecture)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        return await _fetch_bridge_repo(
            client,
            protocol="Axelar",
            source=self.name,
            repo=AXELAR_REPO,
            ref=AXELAR_REF,
            include=("contracts/",),
            limit=limit,
        )


class CCIPSource(Source):
    """Chainlink CCIP router/onramp/offramp + token pool contracts."""

    name = "ccip"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Chainlink CCIP cross-chain token transfer contracts (archived repo)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        return await _fetch_bridge_repo(
            client,
            protocol="Chainlink CCIP",
            source=self.name,
            repo=CCIP_REPO,
            ref=CCIP_REF,
            include=("contracts/src/v0.8/ccip/",),
            limit=limit,
            archived=True,
        )


__all__ = [
    "AXELAR_REF",
    "AXELAR_REPO",
    "CCIP_REF",
    "CCIP_REPO",
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "LAYERZERO_REF",
    "LAYERZERO_REPO",
    "AxelarSource",
    "CCIPSource",
    "LayerZeroSource",
    "bridge_contract_to_doc",
]
