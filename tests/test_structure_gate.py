#!/usr/bin/env python3
"""Tests for the firm ≤200-line structure gate (shrink-only ratchet)."""

import json
import subprocess
import sys
from pathlib import Path

# Repository root: parent of the directory containing this file (tests/ -> repo root)
REPO_ROOT = Path(__file__).resolve().parent.parent


def run_script(args, repo=None):
    """Run check-structure.py; return (returncode, stdout, stderr)."""
    cmd = [sys.executable, "scripts/check-structure.py"]
    if repo is not None:
        cmd.extend(["--repo", str(repo)])
    cmd.extend(args)
    # nosec: B603
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)  # noqa: S603
    return result.returncode, result.stdout, result.stderr


def _mk_src(tmp_path):
    src = tmp_path / "src" / "rugbrain"
    src.mkdir(parents=True)
    return src


def _write_baseline(tmp_path, files):
    path = tmp_path / "scripts" / "structure-baseline.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"files": files}, indent=1))


def test_clean_tree_passes(tmp_path):
    """A clean tree with only small files passes."""
    src = _mk_src(tmp_path)
    (src / "small.py").write_text("print('hello')\n")
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass, got fail: {out} {_err}"
    assert "structure gate: PASS" in out


def test_over_500_lines_fails(tmp_path):
    """A new >500-line file fails even without a baseline."""
    src = _mk_src(tmp_path)
    (src / "big.py").write_text("# comment\n" * 501)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc != 0, f"Expected fail, got pass: {out}"
    assert "NEW file >500" in out
    assert "FAIL (size ratchet):" in out


def test_over_200_non_allowlisted_fails(tmp_path):
    """Firm policy: a 201-line file NOT in the allowlist fails."""
    src = _mk_src(tmp_path)
    (src / "over.py").write_text("# line\n" * 201)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc != 0, f"Expected fail for >200 non-allowlisted, got: {out}"
    assert "NEW file >200" in out


def test_exactly_200_is_advisory(tmp_path):
    """Exactly 200 lines is not >200 — advisory only, passes."""
    src = _mk_src(tmp_path)
    (src / "edge.py").write_text("# line\n" * 200)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass at exactly 200, got: {out} {_err}"
    assert "[report]" in out
    assert "200 lines (advisory" in out


def test_over_150_is_advisory(tmp_path):
    """Files >150 (but ≤200) are reported, never fatal."""
    src = _mk_src(tmp_path)
    (src / "medium.py").write_text("# line\n" * 180)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected advisory pass, got fail: {out} {_err}"
    assert "[report]" in out
    assert "180 lines (advisory" in out


def test_allowlisted_file_growth_fails(tmp_path):
    """An allowlisted file that grows past its recorded size fails."""
    src = _mk_src(tmp_path)
    _write_baseline(tmp_path, {"src/rugbrain/growth.py": 250})
    (src / "growth.py").write_text("# line\n" * 300)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc != 0, f"Expected fail due to growth, got pass: {out}"
    assert "allowlisted file grew" in out


def test_allowlisted_file_shrink_flagged_for_removal(tmp_path):
    """A baselined file that shrank ≤200 is a [remove] candidate and passes."""
    src = _mk_src(tmp_path)
    _write_baseline(tmp_path, {"src/rugbrain/shrunk.py": 205})
    (src / "shrunk.py").write_text("# line\n" * 190)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass for shrink, got: {out} {_err}"
    assert "[shrink]" in out
    assert "[remove]" in out
    assert "shrunk.py" in out


def test_vanished_baseline_entry_flagged_for_removal(tmp_path):
    """A baselined file that no longer exists is a [remove] candidate."""
    _mk_src(tmp_path)
    _write_baseline(tmp_path, {"src/rugbrain/nonexistent.py": 420})
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass, got: {out} {_err}"
    assert "[remove]" in out
    assert "nonexistent.py" in out


def test_update_baseline_refuses_to_grandfather_new_over_200(tmp_path):
    """--update-baseline refuses to add a brand-new >200-line file."""
    src = _mk_src(tmp_path)
    _write_baseline(tmp_path, {"src/rugbrain/known.py": 300})
    (src / "known.py").write_text("# line\n" * 300)
    (src / "newcomer.py").write_text("# line\n" * 250)
    rc, out, _err = run_script(["--update-baseline"], repo=tmp_path)
    assert rc != 0, f"Expected refusal, got pass: {out}"
    assert "REFUSING to grandfather NEW >200-line files" in out


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
