"""Flashbots MEV architecture sources — mev-boost + mev-boost-relay.

Source repos:

  * ``flashbots/mev-boost``         — the PBS (proposer-builder separation)
    sidecar that connects Ethereum validators to block builders. Docs live in
    ``docs/`` (specification, API, design notes).
  * ``flashbots/mev-boost-relay``   — the MEV-Boost relay implementation:
    block auction API, data availability for builders, payout handling.
    ``ARCHITECTURE.md`` + ``docs/`` describe the system design.

Both normalize markdown docs to doc_type ``mev_architecture`` so the PBS /
block-auction design knowledge is retrievable in the MEV search path.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DOC_TYPE = "mev_architecture"
DEFAULT_COLLECTION = "mev_architecture"

MEV_BOOST_REPO = "flashbots/mev-boost"
MEV_BOOST_REF = "develop"
MEV_BOOST_RELAY_REPO = "flashbots/mev-boost-relay"
MEV_BOOST_RELAY_REF = "main"

MAX_CONTENT = 200_000

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _title_from_path(path: str) -> str:
    name = path.split("/")[-1]
    return re.sub(r"\.md$", "", name, flags=re.I).replace("_", " ").replace("-", " ").strip()


def mev_doc_to_doc(
    *,
    source: str,
    protocol: str,
    repo: str,
    ref: str,
    rel_path: str,
    markdown: str,
) -> IngestDoc | None:
    """Normalize one MEV markdown doc into a ``mev_architecture`` doc."""
    if not markdown.strip():
        return None
    m = _H1_RE.search(markdown)
    title = m.group(1).strip() if m else _title_from_path(rel_path)
    source_url = f"https://github.com/{repo}/blob/{ref}/{rel_path}"
    header = f"{protocol} architecture documentation — {title}.\nPath: {rel_path}\n"
    return IngestDoc(
        content=f"{header}\n{markdown.strip()}"[:MAX_CONTENT],
        doc_id=f"{source}:{rel_path}:{_content_hash(markdown[:4000])}",
        doc_type=DOC_TYPE,
        source=source,
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": protocol,
            "rel_path": rel_path,
            "source_url": source_url,
            "chain": "Ethereum",
            "ecosystem": "MEV",
            "doc_type": DOC_TYPE,
        },
    )


async def _fetch_mev_docs(
    client: httpx.AsyncClient,
    *,
    source: str,
    protocol: str,
    repo: str,
    ref: str,
    include: tuple[str, ...],
    limit: int | None,
) -> list[IngestDoc]:
    entries = await list_repo_files(client, repo, ref)
    if not entries:
        log.warning("%s_no_entries (rate-limited or repo moved)", source)
        return []
    md_entries = max_size(
        pick_files(entries, suffixes=(".md",), include=include),
        limit,
    )
    docs: list[IngestDoc] = []
    for e in md_entries:
        path = str(e.get("path", ""))
        markdown = await fetch_raw(client, repo, ref, path)
        doc = mev_doc_to_doc(
            source=source,
            protocol=protocol,
            repo=repo,
            ref=ref,
            rel_path=path,
            markdown=markdown,
        )
        if doc:
            docs.append(doc)
        if limit and len(docs) >= limit:
            break
    return docs


class MEVBoostSource(Source):
    """Flashbots mev-boost PBS sidecar docs."""

    name = "mev_boost"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Flashbots mev-boost (PBS sidecar) docs — block auction + API design"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        return await _fetch_mev_docs(
            client,
            source=self.name,
            protocol="MEV-Boost",
            repo=MEV_BOOST_REPO,
            ref=MEV_BOOST_REF,
            include=("docs/", "README.md"),
            limit=limit,
        )


class MEVBoostRelaySource(Source):
    """Flashbots mev-boost-relay implementation docs."""

    name = "mev_boost_relay"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Flashbots mev-boost-relay docs — relay architecture + builder API"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        return await _fetch_mev_docs(
            client,
            source=self.name,
            protocol="MEV-Boost Relay",
            repo=MEV_BOOST_RELAY_REPO,
            ref=MEV_BOOST_RELAY_REF,
            include=("docs/", "ARCHITECTURE.md", "README.md"),
            limit=limit,
        )


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "MEV_BOOST_REF",
    "MEV_BOOST_RELAY_REF",
    "MEV_BOOST_RELAY_REPO",
    "MEV_BOOST_REPO",
    "MEVBoostRelaySource",
    "MEVBoostSource",
    "mev_doc_to_doc",
]
