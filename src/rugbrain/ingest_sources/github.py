"""Shared GitHub fetching helpers for programmatic RugBrain sources.

Provides the two calls every GitHub-backed source needs:

  * ``list_repo_files`` — one ``git/trees`` recursive call returns the full
    file list of a repo (cheap, no per-directory pagination).
  * ``fetch_raw``       — raw.githubusercontent.com content fetch.

Plus ``pick_files`` for suffix/directory filtering.

Rate limits: the unauthenticated GitHub core API allows 60 requests/hour
per IP; raw.githubusercontent.com is not subject to the same limit. Set
``GITHUB_TOKEN`` to lift the API limit to 5000/hour. All helpers degrade
gracefully (log + return empty) so a rate-limited run never aborts.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugbrain.ingest_sources.base import USER_AGENT

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

    Returns a list of ``{"path", "size", "url"}`` dicts, or ``[]`` on any
    failure (rate limit, missing ref, network error).
    """
    url = f"{GITHUB_API}/repos/{repo}/git/trees/{ref}?recursive=1"
    try:
        r = await client.get(url, headers=github_api_headers())
        if r.status_code != 200:
            log.warning("github_tree_failed repo=%s ref=%s status=%d", repo, ref, r.status_code)
            return []
        entries = r.json().get("tree") or []
        return [
            {"path": e.get("path", ""), "size": int(e.get("size") or 0)}
            for e in entries
            if e.get("type") == "blob"
        ]
    except Exception as e:
        log.warning("github_tree_error repo=%s ref=%s err=%s", repo, ref, str(e)[:200])
        return []


async def fetch_raw(client: httpx.AsyncClient, repo: str, ref: str, path: str) -> str:
    """Fetch a raw file from raw.githubusercontent.com; '' on failure."""
    url = f"{GITHUB_RAW}/{repo}/{ref}/{path}"
    try:
        r = await client.get(url)
        if r.status_code != 200:
            log.debug("github_raw_failed path=%s status=%d", path, r.status_code)
            return ""
        return r.text or ""
    except Exception as e:
        log.warning("github_raw_error path=%s err=%s", path, str(e)[:200])
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


__all__ = [
    "GITHUB_API",
    "GITHUB_RAW",
    "fetch_raw",
    "github_api_headers",
    "list_repo_files",
    "max_size",
    "pick_files",
]
