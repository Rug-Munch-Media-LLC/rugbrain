#!/usr/bin/env python3
"""Test for import-linter gate."""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_import_linter_available_and_passes():
    """Run lint-imports with the project config; skip if not installed."""
    # Prefer the PROJECT's linter. A system-wide `lint-imports` runs under a
    # different interpreter and cannot import `rugbrain`, so it exits 1 with
    # "Could not find package" — a failure that says nothing about the
    # contract and hides every real violation behind it.
    project_bin = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "lint-imports"
    lint_imports_path = str(project_bin) if project_bin.exists() else shutil.which("lint-imports")
    if not lint_imports_path:
        pytest.skip("lint-imports not found")
    # Try to run lint-imports
    try:
        # nosec: B603
        result = subprocess.run(  # noqa: S603
            [lint_imports_path, "--config", ".importlinter"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
    except FileNotFoundError:
        logging.getLogger(__name__).debug("lint-imports vanished mid-run")
        pytest.skip("lint-imports not found")
    # If we get here, the command was found
    assert result.returncode == 0, (
        f"lint-imports failed:\n{result.stdout or result.stderr}"
    )


if __name__ == "__main__":
    # Allow running the test directly for debugging
    sys.exit(pytest.main([__file__, "-v"]))
