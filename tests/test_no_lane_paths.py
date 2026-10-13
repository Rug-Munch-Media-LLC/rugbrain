"""No shipped script may hardcode a LANE WORKTREE path.

An absolute `/srv/work/worktrees/<agent>/<repo>` path inside committed code is a
time bomb: the lane is released and deleted, and the script then reads a
missing directory and reports a confident, wrong result (that is how the
polymarket ingest reported 14 files from a directory that no longer exists).
Data locations must be repo-relative or come from an env override.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
#: only real code paths matter; a prose mention of the failure is fine
LANE_PATH = re.compile(r'["\']/srv/work/worktrees/[^"\']+["\']')


def _scripts() -> list[Path]:
    return sorted(p for p in (REPO / "scripts").rglob("*.py"))


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: p.name)
def test_no_hardcoded_lane_path(path: Path) -> None:
    hits = [
        line.strip()
        for line in path.read_text(errors="replace").splitlines()
        if LANE_PATH.search(line) and not line.strip().startswith(("#", "*", '"""'))
    ]
    assert not hits, f"{path.name} hardcodes a lane path: {hits[:2]}"
