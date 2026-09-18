#!/usr/bin/env python3
"""Test for import-linter gate."""

import logging
import shutil
import subprocess
import sys

import pytest


def test_import_linter_available_and_passes():
    """Run lint-imports with the project config; skip if not installed."""
    lint_imports_path = shutil.which("lint-imports")
    if not lint_imports_path:
        pytest.skip("lint-imports not found in PATH")
    # Try to run lint-imports
    try:
        # nosec: B603
        result = subprocess.run(  # noqa: S603
            [lint_imports_path, "--config", ".importlinter"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        logging.getLogger(__name__).debug("lint-imports vanished mid-run")
        pytest.skip("lint-imports not found")
    # If we get here, the command was found
    assert result.returncode == 0, f"lint-imports failed: {result.stderr}"


if __name__ == "__main__":
    # Allow running the test directly for debugging
    sys.exit(pytest.main([__file__, "-v"]))
