"""Repomix as a *gated* analysis helper: pack a tree, summarise its structure.

Answers "understand this repo" without dumping a 2 MB artifact into an agent's
context: the caller gets file/extension/top-dir counts plus a bounded preview.
It never runs blind — it needs ``allow=True`` AND ``RUGBRAIN_REPOMIX=1`` AND a
tree under the file and byte caps, so a stray call on a huge mono-repo refuses
instead of burning minutes and tokens.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from pathlib import Path

MAX_FILES = 500
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 180
_FILE_RE = re.compile(r'<file path="([^"]+)"')
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".mypy_cache"}


def repomix_enabled() -> bool:
    """True only when the operator opted in for this process."""
    return os.environ.get("RUGBRAIN_REPOMIX", "").lower() in {"1", "true", "yes"}


def _count_files(root: Path) -> int:
    total = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        total += len(filenames)
    return total


def _summarise(packed: str) -> dict:
    paths = _FILE_RE.findall(packed)
    by_ext = Counter(Path(path).suffix or "<none>" for path in paths)
    by_top = Counter(path.split("/")[0] for path in paths)
    return {
        "packed_files": len(paths),
        "by_extension": dict(by_ext.most_common(8)),
        "by_top_dir": dict(by_top.most_common(8)),
        "sample_paths": paths[:10],
    }


async def pack_repo(
    path: str,
    *,
    allow: bool = False,
    max_files: int = MAX_FILES,
    max_bytes: int = MAX_BYTES,
) -> dict:
    """Pack *path* with repomix and return a structure summary or a refusal."""
    root = Path(path).resolve()
    if not allow:
        return {"ok": False, "refusal": "flag_required", "path": str(root)}
    if not repomix_enabled():
        return {"ok": False, "refusal": "flag_disabled", "path": str(root)}
    if not root.is_dir():
        return {"ok": False, "refusal": "not_a_dir", "path": str(root)}
    files = _count_files(root)
    if files > max_files:
        return {"ok": False, "refusal": "too_many_files", "files": files, "cap": max_files}
    proc = await asyncio.create_subprocess_exec(
        "repomix", "--stdout", "--compress", str(root),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
    if proc.returncode != 0:
        return {"ok": False, "refusal": "repomix_failed", "detail": err.decode("utf-8", "ignore")[:200]}
    if len(out) > max_bytes:
        return {"ok": False, "refusal": "pack_too_large", "bytes": len(out), "cap": max_bytes}
    text = out.decode("utf-8", "ignore")
    return {
        "ok": True, "path": str(root), "bytes": len(out), "files": files,
        "summary": _summarise(text), "preview": text[:1500],
    }
