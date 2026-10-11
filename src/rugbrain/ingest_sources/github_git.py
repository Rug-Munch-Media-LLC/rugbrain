"""GitHub-API-free file listing via ``git clone`` fallback.

Used when the GitHub REST API (60 req/h unauthenticated) is rate-limited.
Avoids hammering the API by cloning with ``--filter=blob:none`` (tree only,
no blob content) and listing paths with ``git ls-tree``.  File *content* is
still fetched separately via ``fetch_raw`` (raw.githubusercontent.com,
unthrottled) — this module only replaces the tree-listing call.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Any

_GIT_BIN: str | None = shutil.which("git")
_RM_BIN: str | None = shutil.which("rm")


def _clone_env() -> dict[str, str]:
    """Env for git subprocess, injecting PROXY_URL as http(s)_proxy."""
    env = os.environ.copy()
    proxy = os.getenv("PROXY_URL", "").strip()
    if proxy:
        env.setdefault("https_proxy", proxy)
        env.setdefault("http_proxy", proxy)
    return env


def _clone_repo(repo: str, ref: str, tmp: str) -> bool:
    """Shallow blobless clone of *repo* at *ref* into *tmp*.  True on success."""
    url = f"https://github.com/{repo}.git"
    if not _GIT_BIN:
        return False
    try:
        subprocess.run(  # noqa: S603 — hardcoded args, repo/ref from module constants
            [
                _GIT_BIN,
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--no-checkout",
                "--branch",
                ref,
                url,
                tmp,
            ],
            capture_output=True,
            timeout=120,
            check=True,
            env=_clone_env(),
        )
        return True
    except Exception:
        return False


def _ls_tree(tmp: str) -> list[dict[str, Any]]:
    """Enumerate blob paths from the clone via ``git ls-tree -r HEAD``."""
    if not _GIT_BIN:
        return []
    r = subprocess.run(  # noqa: S603 — hardcoded args
        [_GIT_BIN, "-C", tmp, "ls-tree", "-r", "HEAD"],
        capture_output=True,
        timeout=60,
        text=True,
        check=True,
        env=_clone_env(),
    )
    out: list[dict[str, Any]] = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[1].strip():
            out.append({"path": parts[1].strip(), "size": 1})
    return out


async def list_repo_files_git(repo: str, ref: str) -> list[dict[str, Any]]:
    """List every blob in *repo* at *ref* via git clone.

    Returns the same ``[{"path", "size"}]`` shape as ``list_repo_files``.
    ``size`` is set to 1 (``--filter=blob:none`` means real blob sizes are
    unavailable; downstream ``max_size`` only filters ``size <= 0``).
    Empty list on any failure — caller treats it as a rate-limited/missing repo.
    """
    if not _GIT_BIN:
        return []
    tmp = tempfile.mkdtemp(prefix="rb_git_")
    try:
        if not await asyncio.to_thread(_clone_repo, repo, ref, tmp):
            return []
        return await asyncio.to_thread(_ls_tree, tmp)
    finally:
        if _RM_BIN:
            await asyncio.to_thread(
                lambda: subprocess.run(  # noqa: S603
                    [_RM_BIN, "-rf", tmp], capture_output=True, timeout=30
                )
            )


__all__ = ["list_repo_files_git"]
