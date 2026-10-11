"""Shared GitHub fetching helpers for programmatic RugBrain sources.

Provides the two calls every GitHub-backed source needs:

  * ``list_repo_files`` — one ``git/trees`` recursive call returns the full
    file list of a repo (cheap, no per-directory pagination).  Falls back to
    ``git clone --filter=blob:none`` when the API is rate-limited.
  * ``fetch_raw``       — raw.githubusercontent.com content fetch.

Plus ``pick_files`` for suffix/directory filtering.

Rate limits: the unauthenticated GitHub core API allows 60 requests/hour
per IP; raw.githubusercontent.com is not subject to the same limit. Set
``GITHUB_TOKEN`` to lift the API limit to 5000/hour. All helpers degrade
gracefully (return empty, never raise) so a rate-limited run never aborts.

An empty return is NOT the same as a healthy empty source: every HTTP error
is recorded on the :mod:`rugbrain.ingest_sources.fetch_state` channel with an
operator-readable ``reason=`` token (rate_limited / auth_invalid / not_found /
network_error / …), so the caller can fail the run instead of reporting a
green zero. See ``fetch_state`` for the protocol.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugbrain.ingest_sources.base import USER_AGENT
from rugbrain.ingest_sources.fetch_state import (
    classify_exception,
    classify_status,
    raw_not_found_reason,
    record_fetch_error,
)
from rugbrain.ingest_sources.github_git import list_repo_files_git

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"


def github_api_headers() -> dict[str, str]:
    """Headers for api.github.com (optional token from ``GITHUB_TOKEN``)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def list_repo_files(
    client: httpx.AsyncClient,
    repo: str,
    ref: str = "main",
) -> list[dict[str, Any]]:
    """List every blob in *repo* at *ref* via the git trees API.

    Returns a list of ``{"path", "size"}`` dicts, or ``[]`` on any failure
    (rate limit, missing ref, deleted repo, network error) — but a failure is
    *recorded* on the fetch channel, so the caller can tell it apart from a
    repo that genuinely has no blobs.
    """
    url = f"{GITHUB_API}/repos/{repo}/git/trees/{ref}?recursive=1"
    try:
        r = await client.get(url, headers=github_api_headers())
        if r.status_code != 200:
            log.warning("github_tree_failed repo=%s ref=%s status=%d", repo, ref, r.status_code)
            record_fetch_error(
                classify_status(r.status_code, headers=r.headers, body=r.text),
                f"list_repo_files repo={repo} ref={ref} status={r.status_code}",
            )
            # Fallback: git clone (avoids GitHub API 60 req/h rate limit)
            entries = await list_repo_files_git(repo, ref)
            if entries:
                log.info("list_repo_files_git_fallback_ok repo=%s entries=%d", repo, len(entries))
                return entries
            return []
        entries = r.json().get("tree") or []
        return [
            {"path": e.get("path", ""), "size": int(e.get("size") or 0)}
            for e in entries
            if e.get("type") == "blob"
        ]
    except Exception as e:
        log.warning("github_tree_error repo=%s ref=%s err=%s", repo, ref, str(e)[:200])
        record_fetch_error(
            classify_exception(e), f"list_repo_files repo={repo} ref={ref} err={str(e)[:160]}"
        )
        # Fallback: git clone
        entries = await list_repo_files_git(repo, ref)
        if entries:
            log.info("list_repo_files_git_fallback_ok repo=%s entries=%d", repo, len(entries))
            return entries
        return []


async def fetch_raw(client: httpx.AsyncClient, repo: str, ref: str, path: str) -> str:
    """Fetch a raw file from raw.githubusercontent.com; ``''`` on failure.

    A failure is recorded on the fetch channel (raw misses use their own
    reason token — raw is unauthenticated and unthrottled, so a raw 404 says
    nothing about whether the api.github.com path is live).
    """
    url = f"{GITHUB_RAW}/{repo}/{ref}/{path}"
    try:
        r = await client.get(url)
        if r.status_code != 200:
            log.debug("github_raw_failed path=%s status=%d", path, r.status_code)
            record_fetch_error(
                raw_not_found_reason(r.status_code),
                f"fetch_raw repo={repo} ref={ref} path={path} status={r.status_code}",
            )
            return ""
        return r.text or ""
    except Exception as e:
        log.warning("github_raw_error path=%s err=%s", path, str(e)[:200])
        record_fetch_error(
            classify_exception(e), f"fetch_raw repo={repo} ref={ref} path={path} err={str(e)[:160]}"
        )
        return ""


def pick_files(
    entries: list[dict[str, Any]],
    *,
    suffixes: tuple[str, ...] = (),
    include: tuple[str, ...] = (),
    exclude_dirs: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Filter *entries* (from ``list_repo_files``) by suffix/path rules.

    ``suffixes``  — keep files ending with one of these (e.g. ``(".sol",)``).
    ``include``   — keep files whose path contains one of these substrings.
    ``exclude_dirs`` — drop files under any of these directory prefixes.
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        path = str(e.get("path", ""))
        if not path:
            continue
        if exclude_dirs and any(path.startswith(f"{d}/") or path == d for d in exclude_dirs):
            continue
        if include and not any(s in path for s in include):
            continue
        if suffixes and not path.endswith(suffixes):
            continue
        out.append(e)
    return out


def max_size(entries: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """Keep at most *limit* entries, skipping suspiciously large blobs.

    ``size <= 0`` (truncated tree payloads / generated dirs) is dropped,
    protecting downstream embedding from multi-MB generated files.
    """
    kept: list[dict[str, Any]] = []
    for e in entries:
        size = int(e.get("size") or 0)
        if size <= 0:
            continue
        kept.append(e)
    if limit is not None:
        kept = kept[:limit]
    return kept


def tree_has_blob(entries: list[dict[str, Any]], suffix: str) -> bool:
    """True when *entries* contains at least one blob whose path ends with *suffix*.

    A liveness assert must target a CONCRETE file, never a directory: a
    bare-prefix/directory fetch returns 200-with-nothing for a deleted repo
    on some hosts, so "the directory read fine" proves nothing.
    """
    return any(str(e.get("path", "")).endswith(suffix) for e in entries)


__all__ = [
    "GITHUB_API",
    "GITHUB_RAW",
    "fetch_raw",
    "github_api_headers",
    "list_repo_files",
    "list_repo_files_git",
    "max_size",
    "pick_files",
    "tree_has_blob",
]
