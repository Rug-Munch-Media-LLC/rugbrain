#!/usr/bin/env python3
"""Tests for the structure gate script."""

import json
import subprocess
import sys
from pathlib import Path

# Repository root: parent of the directory containing this file (tests/ -> repo root)
REPO_ROOT = Path(__file__).resolve().parent.parent


def run_script(args, repo=None):
    """Run the check-structure.py script.
    Args:
        args: list of arguments to pass to the script.
        repo: if provided, use --repo <repo>; otherwise, use the current directory.
    Returns:
        (returncode, stdout, stderr)
    """
    cmd = [sys.executable, "scripts/check-structure.py"]
    if repo is not None:
        cmd.extend(["--repo", str(repo)])
    cmd.extend(args)
    # nosec: B603
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)  # nosec: B603,S603
    return result.returncode, result.stdout, result.stderr


def test_clean_tree_passes(tmp_path):
    """A clean tree with no large files should pass."""
    # Create a small Python file
    src = tmp_path / "src" / "rugbrain"
    src.mkdir(parents=True)
    (src / "small.py").write_text("print('hello')\n")
    # Run the script
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass, got fail: {out} {_err}"
    assert "structure gate: PASS" in out


def test_over_500_lines_fails(tmp_path):
    """A file >500 lines should fail unless allowlisted."""
    src = tmp_path / "src" / "rugbrain"
    src.mkdir(parents=True)
    big_file = src / "big.py"
    big_file.write_text("# comment\n" * 501)  # 501 lines
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc != 0, f"Expected fail, got pass: {out}"
    assert "NEW file >500" in out or "lines (NEW file >{FAIL_THRESHOLD}" in out
    assert "FAIL (size ratchet):" in out


def test_allowlisted_file_growth_fails(tmp_path):
    """An allowlisted file that grows should fail."""
    src = tmp_path / "src" / "rugbrain"
    src.mkdir(parents=True)
    # Create a baseline with a file at 250 lines (>=200, <500)
    baseline = {
        "thresholds": {"report": 200, "fail": 500},
        "files": {"src/rugbrain/growth.py": 250},
    }
    baseline_path = tmp_path / "scripts" / "structure-baseline.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps(baseline, indent=1))
    # Now make the file 300 lines (growth)
    growth_file = src / "growth.py"
    growth_file.write_text("# line\n" * 300)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc != 0, f"Expected fail due to growth, got pass: {out}"
    assert "allowlisted file grew" in out


def test_over_200_lines_reports_but_not_fail(tmp_path):
    """A file between 200 and 500 lines should be reported but not fail."""
    src = tmp_path / "src" / "rugbrain"
    src.mkdir(parents=True)
    medium_file = src / "medium.py"
    medium_file.write_text("# line\n" * 250)  # 250 lines
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass (report only), got fail: {out} {_err}"
    assert "250 lines (new >=" in out or "250 lines (new >=" in out
    assert "[report]" in out


def test_allowlist_file_itself_handling(tmp_path):
    """The baseline file itself should be handled correctly."""
    src = tmp_path / "src" / "rugbrain"
    src.mkdir(parents=True)
    # Create a baseline that includes a non-existent file (should be ignored)
    baseline = {
        "thresholds": {"report": 200, "fail": 500},
        "files": {"src/rugbrain/nonexistent.py": 100},
    }
    baseline_path = tmp_path / "scripts" / "structure-baseline.json"
    baseline_path.parent.mkdir(parents=True)
    baseline_path.write_text(json.dumps(baseline, indent=1))
    # Run the script
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass, got fail: {out} {_err}"
    # The nonexistent file should not cause a failure; it should just be missing from current scan
    # We expect a shrinkable line maybe
    assert "structure gate: PASS" in out or "PASS" in out


if __name__ == "__main__":
    # Allow running the test directly for debugging
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
