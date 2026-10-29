"""Generic documentation source over a git repository at a PINNED ref.

``devdocs`` is a declared-URL fetcher with no repo concept, so "repo X at commit
Y, licence per page" was hand-rolled in every docs lane. This is that one source:
tree walk -> per-file fetch -> frontmatter strip -> heading chunks -> one
``IngestDoc`` per section. ``ref`` must be a sha or tag (a floating branch makes
a citation uncheckable); ``licence="read_from_repo"`` reads ``LICENSE*`` at the
same sha, and a page without one is still ingested as ``publishable=False``.
Chunking and the 4096-d embedding stay downstream in ``store_documents``.

Heading/chunk text handling lives in :mod:`rugbrain.ingest_sources.repo_docs_text`
and is re-exported here, so ``from ...repo_docs import chunk_sections`` keeps
working for callers and tests.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files
from rugbrain.ingest_sources.repo_docs_text import (
    chunk_sections,
    classify_page_kind,
    detect_licence,
    split_sections,
)
from rugbrain.ingest_sources.shared.http import HttpClient

__all__ = [
    "DEFAULT_EXTENSIONS",
    "RepoDocsSource",
    "chunk_sections",
    "classify_page_kind",
    "collect",
    "detect_licence",
    "read_licence",
    "split_sections",
]

log = logging.getLogger(__name__)
DOC_TYPE = "repo_documentation"
DEFAULT_EXTENSIONS = (".md", ".mdx", ".adoc")
FLOATING_REFS = {"main", "master", "head", "develop", "trunk", "latest"}
NOISE = ("changelog", "release-notes", "releasenotes", "blog", "i18n", "node_modules", "vendor")
UNPUBLISHABLE = {"", "UNKNOWN", "NOASSERTION", "UNLICENSED", "PROPRIETARY"}
REGISTRY: dict[str, type] = {}
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n.*?\r?\n---[ \t]*\r?\n", re.DOTALL)

_PRIORITY = {"reference": 0, "guide": 1, "landing": 2, "other": 3}


def _page_priority(path: str) -> tuple[int, str]:
    return (_PRIORITY.get(classify_page_kind(path), 3), path)


def register(name: str, **attrs: Any):
    def _decorate(cls: type) -> type:
        cls.name = name
        for key, value in attrs.items():
            setattr(cls, key, value)
        REGISTRY[name] = cls
        return cls

    return _decorate


async def read_licence(client: HttpClient, repo: str, ref: str, tree) -> tuple[str, str]:
    """Read the repo's licence file at *ref*; returns (spdx-or-empty, path)."""
    for path in sorted(str(e.get("path", "")) for e in tree):
        if "/" in path or not path.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
            continue
        try:
            return detect_licence(await fetch_raw(client, repo, ref, path)), path
        except Exception as exc:
            log.warning("repo_docs_licence_failed path=%s err=%s", path, str(exc)[:120])
    return "", ""


async def collect(client: HttpClient, spec: RepoDocsSource, limit: int | None = None):
    """Walk *spec.repo* at *spec.ref*; return ``(docs, stats)``."""
    stats: dict[str, Any] = {
        "listed": 0,
        "files": 0,
        "pages": 0,
        "failed": 0,
        "by_page_kind": {},
        "licence": "",
        "publishable": False,
    }
    tree = await list_repo_files(client, spec.repo, spec.ref)
    stats["listed"] = len(tree)
    if not tree:
        from rugbrain.ingest_sources.fetch_state import take_fetch_error

        recorded = take_fetch_error()
        stats["error"] = recorded.reason if recorded is not None else "empty_tree_or_ref_not_found"
        return [], stats
    licence, licence_path = ("", "")
    if spec.licence == "read_from_repo":
        licence, licence_path = await read_licence(client, spec.repo, spec.ref, tree)
    else:
        licence = spec.licence
    publishable = licence.upper() not in UNPUBLISHABLE
    stats.update(licence=licence, licence_path=licence_path, publishable=publishable)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    cap = spec.max_pages if limit is None else min(limit, spec.max_pages)
    paths = [
        p
        for p in (str(e.get("path", "")).lstrip("/") for e in tree)
        if p.lower().endswith(spec.extensions)
        and not any(x in p.lower() for x in NOISE)
        and not any(fnmatch.fnmatch(p.lower(), pat.lower()) for pat in spec.exclude)
        and (not spec.include or any(fnmatch.fnmatch(p, pat) for pat in spec.include))
    ]
    docs: list[IngestDoc] = []
    for path in sorted(paths, key=_page_priority):
        if stats["pages"] >= cap:
            break
        try:
            raw = await fetch_raw(client, spec.repo, spec.ref, path)
        except Exception as exc:
            stats["failed"] += 1
            log.warning("repo_docs_fetch_failed path=%s err=%s", path, str(exc)[:120])
            continue
        body = _FRONTMATTER_RE.sub("", (raw or "").replace("\r\n", "\n"), count=1).strip("\n")
        sections = split_sections(body)
        kind = classify_page_kind(path, sections[0][0] if sections else "")
        stats["files"] += 1
        for title, chunk in chunk_sections(sections):
            if stats["pages"] >= cap:
                break
            docs.append(
                IngestDoc(
                    content=f"{spec.repo} @ {spec.ref[:12]} — {path}\nLicence: {licence or 'unknown'}\n\n{chunk}",
                    doc_id=f"repo_docs:{spec.repo}:{spec.ref[:12]}:{path}:"
                    f"{hashlib.sha256(chunk.encode()).hexdigest()[:16]}",
                    doc_type=DOC_TYPE,
                    source=spec.name,
                    title=title or path,
                    url=path,
                    metadata={
                        "repo": spec.repo,
                        "ref": spec.ref,
                        "path": path,
                        "section": title,
                        "licence": licence,
                        "page_kind": kind,
                        "fetched_at": fetched_at,
                        "publishable": publishable,
                        "collection": spec.collection,
                        "licence_path": licence_path,
                    },
                )
            )
            stats["pages"] += 1
            stats["by_page_kind"][kind] = stats["by_page_kind"].get(kind, 0) + 1
    return docs, stats


@register("repo_docs")
class RepoDocsSource(Source):
    name = "repo_docs"
    doc_type = DOC_TYPE
    default_collection = "repo_docs"
    description = "generic pinned-repo docs (md/mdx/adoc, per-page licence, heading chunks)"

    def __init__(
        self,
        repo: str,
        ref: str,
        include=("**/*",),
        exclude=(),
        max_pages: int = 200,
        extensions=DEFAULT_EXTENSIONS,
        licence: str = "read_from_repo",
        collection: str = "repo_docs",
    ):
        value = (ref or "").strip()
        if not repo or "/" not in repo:
            raise ValueError("repo_docs: repo must be 'owner/name'")
        if not value:
            raise ValueError("repo_docs: ref is required (sha or tag)")
        if value.lower() in FLOATING_REFS:
            raise ValueError(f"repo_docs: ref {value!r} floats; pin a sha or tag")
        self.repo, self.ref, self.include = repo, value, tuple(include)
        self.exclude, self.extensions = tuple(exclude), tuple(extensions)
        self.max_pages, self.licence, self.collection = int(max_pages), licence, collection

    async def _fetch(self, client: HttpClient, limit: int | None) -> list[IngestDoc]:
        docs, _stats = await collect(client, spec=self, limit=limit)
        return docs
