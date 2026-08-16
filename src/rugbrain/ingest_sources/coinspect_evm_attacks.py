"""coinspect/learn-evm-attacks — Foundry EVM exploit reproduction library.

Source: https://github.com/coinspect/learn-evm-attacks (default branch
``master``, verified live) — Coinspect's Foundry reproduction library of
real EVM exploits with READMEs + diagrams. Repo layout:
``test/<Category>/<Protocol>/README.md`` (e.g.
``test/Access_Control/DAOMaker/README.md``) where the category names the
vulnerability class.

One ``IngestDoc`` per exploit README into ``defi_exploits`` (doc_type
``exploit_poc``) — the protocol + category come from the path, the body is
the README text.

Design rules (consistent with ``base.py`` / ``github.py``):

  * Fetch → normalize → emit; fail-open per file.
  * Deterministic ``doc_id`` (``coinspect_learn_evm_attacks:<rel_path>``)
    keeps re-ingestion idempotent.
  * Bounded by ``limit`` (default cap 2,000).
  * Credentials: env-only (``GITHUB_TOKEN`` / ``PROXY_URL``).
"""

from __future__ import annotations

import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "defi_exploits"
DEFAULT_CAP = 2_000
REPO = "coinspect/learn-evm-attacks"
REF = "master"  # verified live on 2026-08-16 (77342c4)
README_INCLUDE = "test/"

_PATH_RE = re.compile(r"^test/([^/]+)/([^/]+)/README\.md$")


def _clean_category(segment: str) -> str:
    """Path category segment → lowercase vulnerability class label."""
    return re.sub(r"[^a-z0-9]+", "-", segment.lower()).strip("-")


def readme_to_doc(rel_path: str, markdown: str) -> IngestDoc | None:
    """Normalize one ``test/<Category>/<Protocol>/README.md`` into an IngestDoc."""
    m = _PATH_RE.match(rel_path)
    if not m:
        return None
    category_raw, protocol_raw = m.group(1), m.group(2)
    body = (markdown or "").strip()
    if not body:
        return None
    category = _clean_category(category_raw)
    protocol = protocol_raw.replace("_", " ").strip()
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    content = (
        f"Coinspect EVM attack reproduction — {protocol}.\n"
        f"Vulnerability category: {category}\n"
        f"PoC: {source_url}\n\n"
        f"{body[:8000]}"
    )
    return IngestDoc(
        content=content,
        doc_id=f"coinspect_learn_evm_attacks:{rel_path}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": protocol,
            "category": category,
            "vulnerability_class": category,
            "severity": "high",
            "chain": "EVM",
            "ecosystem": "EVM",
            "source_url": source_url,
            "rel_path": rel_path,
            "doc_type": "exploit_poc",
        },
        doc_type="exploit_poc",
        source="coinspect_learn_evm_attacks",
        title=f"{protocol} — {category} exploit reproduction",
        url=source_url,
    )


class CoinspectEVMAttacksSource(Source):
    """coinspect/learn-evm-attacks — Foundry EVM exploit reproductions.

    Lists the repo tree, fetches ``test/**/README.md`` files (bounded by
    ``limit``), and emits one ``IngestDoc`` per exploit into
    ``defi_exploits`` (doc_type ``exploit_poc``).
    """

    name = "coinspect_learn_evm_attacks"
    doc_type = "exploit_poc"
    default_collection = DEFAULT_COLLECTION
    description = "Coinspect learn-evm-attacks — Foundry EVM exploit reproductions with READMEs (defi_exploits)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("coinspect_tree_failed (rate-limited or repo moved)")
            return []
        readmes = max_size(
            pick_files(entries, suffixes=("README.md",), include=(README_INCLUDE,)), cap
        )
        docs: list[IngestDoc] = []
        for e in readmes:
            path = str(e.get("path", ""))
            markdown = await fetch_raw(client, REPO, REF, path)
            doc = readme_to_doc(path, markdown)
            if doc:
                docs.append(doc)
            if len(docs) >= cap:
                break
        if not docs:
            log.warning("coinspect_fetch_empty")
        else:
            log.info("coinspect_feed_ok exploits=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "CoinspectEVMAttacksSource",
    "readme_to_doc",
]
