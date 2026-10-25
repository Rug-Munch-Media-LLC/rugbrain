"""Clone a declared docs repo BY EXIT CODE, pin the ref, read its licence.

Root cause of the 2026-10-25 empty run: clone success was inferred from
``subprocess.run(...).stdout``. ``git clone`` writes its progress to STDERR, so a
SUCCESSFUL clone looked empty and was then discarded with ``rm -rf``. A clone is
successful iff ``returncode == 0``; stdout is not a signal. A failed checkout is
KEPT on disk for inspection — deleting the evidence is how the defect survived.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from defi_docs_config import (
    LICENCE_FILES, SPDX_SOURCES, classify_licence, classify_spdx, spdx_from_text,
)


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=900)


def _show(repo: Path, ref: str, name: str) -> str:
    """Content AT the pinned commit — not whatever the worktree drifted to."""
    result = git("show", f"{ref}:{name}", cwd=repo)
    return result.stdout if result.returncode == 0 else ""


def clone(protocol: str, cands: list[str], root: Path) -> tuple[Path | None, list[dict]]:
    """(path | None, attempts). Success is rc == 0. Never rm -rf a failed tree."""
    base = root / protocol
    attempts: list[dict] = []
    if (base / ".git").is_dir():
        return base, attempts
    for index, url in enumerate(cands):
        if index == 0:
            dest = base
        else:
            dest = base.with_name(f"{protocol}.cand{index}")
        if dest.exists():
            dest = dest.with_name(f"{dest.name}.kept{int(time.time())}")
        result = git("clone", "--depth", "1", "--single-branch", url, str(dest))
        ok = result.returncode == 0
        tail = (result.stderr or "").strip().splitlines()[-1:] or [""]
        attempts.append({"url": url, "rc": result.returncode, "ok": ok,
                         "stderr": tail[0][:200], "tree": str(dest)})
        if ok:
            return dest, attempts
        print(f"clone_fail rc={result.returncode} {url} :: {tail[0][:200]}", file=sys.stderr, flush=True)
    return None, attempts


def licence_at_ref(repo: Path, ref: str) -> tuple[str, bool, str, str]:
    """(spdx, publishable, note, source). Missing licence => publishable=False, never guessed."""
    for name in LICENCE_FILES:
        text = _show(repo, ref, name)
        if text.strip():
            spdx, publishable, note = classify_licence(text)
            return spdx, publishable, note, name
    for name in SPDX_SOURCES:
        text = _show(repo, ref, name)
        if text.strip():
            spdx = spdx_from_text(text)
            if spdx:
                classified, publishable, note = classify_spdx(spdx)
                return classified, publishable, note, f"{name}:spdx"
    return "UNKNOWN", False, "no licence file at pinned commit: internal use only", ""
