"""Guards that keep `.importlinter` honest.

Two failure modes are covered, both observed on this repo:

1. **The wrong linter ran.** ``shutil.which("lint-imports")`` found a
   system-wide binary under a different interpreter, which cannot import
   ``rugbrain`` and exits 1 with "Could not find package". That is a failure
   that says nothing about the contract, and it masks every real violation
   behind it. :mod:`test_import_boundaries` now runs the project's own linter.

2. **A stale ignore rule looks like a clean bill of health.** import-linter
   reports "No matches for ignored import" and still exits 0, so a rule can
   name a module that no longer exists and the boundary reads as KEPT when
   nothing was ever checked. A rule here once named
   ``crypto_code_retrieval`` while the real edge was ``chain_parquet`` — a
   fortnight of a boundary that was never actually tested.

The second guard parses lint-imports' own output for unmatched-ignore
warnings, so the allowlist cannot silently rot.
"""

from __future__ import annotations

import configparser
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / ".importlinter"

#: import-linter's wording for an ignore that matched nothing, across versions.
STALE_PATTERNS = (
    re.compile(r"no matches for ignored import", re.IGNORECASE),
    re.compile(r"ignored import.*did not match", re.IGNORECASE),
)


def _project_linter() -> str:
    """The project's own linter, never a system-wide one."""
    local = REPO / ".venv" / "bin" / "lint-imports"
    if local.exists():
        return str(local)
    found = shutil.which("lint-imports")
    if not found:
        pytest.skip("lint-imports not installed")
    return found


def _run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [_project_linter(), "--config", str(CONFIG)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(REPO),
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every ignore_imports entry must still match a real import.

    A rule that matches nothing exits 0 and reads as a clean boundary. That is
    how a dead rule can sit in this file for a fortnight while the dependency
    it was supposed to guard moves somewhere else entirely.
    """
    result = _run()
    output = f"{result.stdout}\n{result.stderr}"
    stale = [
        line.strip() for line in output.splitlines() if any(p.search(line) for p in STALE_PATTERNS)
    ]
    assert not stale, (
        "stale ignore_imports entries (they match nothing, so the boundary is "
        f"unchecked but reads as clean): {stale}"
    )


def test_every_first_party_ignore_entry_names_a_real_module() -> None:
    """Each ignore entry names a first-party module that actually exists.

    Cheaper and more direct than parsing the linter's warnings: a rule naming a
    module that was renamed or deleted can never fire again.
    """
    parser = configparser.ConfigParser()
    parser.read(CONFIG)
    missing: list[str] = []
    for section in parser.sections():
        if not section.startswith("importlinter:contract:"):
            continue
        raw = parser[section].get("ignore_imports", "")
        for line in raw.splitlines():
            entry = line.split("#", 1)[0].strip()
            if not entry or "->" not in entry:
                continue
            for module in (part.strip() for part in entry.split("->")):
                # Only first-party modules live under src/. External packages
                # (httpx, qdrant_client, ...) are named on the right-hand side
                # of these edges and are resolved by the interpreter instead.
                if not module.startswith("rugbrain."):
                    continue
                path = REPO / "src" / Path(*module.split(".")).with_suffix(".py")
                pkg_init = REPO / "src" / Path(*module.split(".")) / "__init__.py"
                if not path.exists() and not pkg_init.exists():
                    missing.append(f"{section}:{module}")
    assert not missing, f"ignore entries naming modules that do not exist: {missing}"
