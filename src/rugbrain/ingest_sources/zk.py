"""Zero-knowledge toolchain sources — Circom + SnarkJS (iden3).

Source repos:

  * ``iden3/circom``  — the Circom arithmetic-circuit compiler. The user
    documentation lives in ``mkdocs/docs/`` (language reference, circuit
    structure, background) and is the highest-value content for retrieval.
  * ``iden3/snarkjs`` — the SnarkJS zk-SNARK proving/verifying toolkit.
    Fetches the repo README + ``doc/*.md`` format specifications (groth16,
    plonk, fflonk zkey formats).

Both normalize markdown docs to doc_type ``zk_circuit``. Sources are
additive and degrade to ``[]`` on failure, matching the other fetchers.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DOC_TYPE = "zk_circuit"
DEFAULT_COLLECTION = "zk_circuits"

CIRCOM_REPO = "iden3/circom"
CIRCOM_REF = "master"
SNARKJS_REPO = "iden3/snarkjs"
SNARKJS_REF = "master"

MAX_CONTENT = 200_000

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _title_from_path(path: str) -> str:
    name = path.split("/")[-1]
    return re.sub(r"\.md$", "", name, flags=re.I).replace("_", " ").replace("-", " ").strip()


def zk_doc_to_doc(
    *,
    source: str,
    protocol: str,
    repo: str,
    ref: str,
    rel_path: str,
    markdown: str,
) -> IngestDoc | None:
    """Normalize one ZK-toolchain markdown doc into a ``zk_circuit`` doc."""
    if not markdown.strip():
        return None
    m = _H1_RE.search(markdown)
    title = m.group(1).strip() if m else _title_from_path(rel_path)
    source_url = f"https://github.com/{repo}/blob/{ref}/{rel_path}"
    header = f"{protocol} documentation — {title}.\nPath: {rel_path}\n"
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
            "chain": "ZK",
            "ecosystem": "ZK",
            "doc_type": DOC_TYPE,
        },
    )


async def _fetch_md_docs(
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
        doc = zk_doc_to_doc(
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


class CircomSource(Source):
    """Circom circuit-compiler documentation (mkdocs/docs)."""

    name = "circom"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Circom arithmetic-circuit compiler docs + language reference"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        return await _fetch_md_docs(
            client,
            source=self.name,
            protocol="Circom",
            repo=CIRCOM_REPO,
            ref=CIRCOM_REF,
            include=("mkdocs/docs/",),
            limit=limit,
        )


class SnarkJSSource(Source):
    """SnarkJS proving/verifying toolkit docs + zkey format specs."""

    name = "snarkjs"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "SnarkJS zk-SNARK toolkit docs + groth16/plonk/fflonk zkey format specs"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        return await _fetch_md_docs(
            client,
            source=self.name,
            protocol="SnarkJS",
            repo=SNARKJS_REPO,
            ref=SNARKJS_REF,
            include=("doc/", "README.md"),
            limit=limit,
        )


__all__ = [
    "CIRCOM_REF",
    "CIRCOM_REPO",
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "SNARKJS_REF",
    "SNARKJS_REPO",
    "CircomSource",
    "SnarkJSSource",
    "zk_doc_to_doc",
]
