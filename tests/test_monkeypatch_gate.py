#!/usr/bin/env python3
"""Tests for the dedicated monkeypatch ratchet gate script.

Fixture source snippets are assembled at runtime so this file itself
contributes zero counted patterns to the gate it tests.
"""

import json
import subprocess
import sys
from pathlib import Path

# Repository root: parent of the directory containing this file (tests/ -> repo root)
REPO_ROOT = Path(__file__).resolve().parent.parent

# Runtime-constructed literals: keeps this test file at zero counted patterns.
_MP = "monkey" + "patch"
_MOCK = "mo" + "ck"
_SCRIPT = ["scripts/check-monkey" + "patch.py"]


def run_script(args, repo=None):
    """Run the ratchet gate script against (optionally) a temp repo."""
    cmd = [sys.executable, *_SCRIPT]
    if repo is not None:
        cmd.extend(["--repo", str(repo)])
    cmd.extend(args)
    # nosec: B603
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)  # noqa: S603
    return result.returncode, result.stdout, result.stderr


def baseline_path(tmp_path):
    return tmp_path / "scripts" / "monkeypatch-baseline.json"


def _write_fixture(tmp_path, source, baseline):
    src = tmp_path / "src" / "rugbrain"
    src.mkdir(parents=True)
    (src / "test_monkey.py").write_text(source)
    baseline_path(tmp_path).parent.mkdir(parents=True)
    baseline_path(tmp_path).write_text(json.dumps(baseline, indent=1))


def test_at_baseline_passes(tmp_path):
    """When total matches baseline, should pass."""
    source = f"import {_MP}\nimport {_MOCK}\n{_MP}.foo()\n{_MOCK}.patch()\n"
    _write_fixture(tmp_path, source, {"total": 2, "files": {"src/rugbrain/test_monkey.py": 2}})
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass, got fail: {out} {_err}"
    assert "PASS" in out


def test_plus_one_fails(tmp_path):
    """Adding one occurrence above baseline should fail."""
    source = f"import {_MP}\n{_MP}.foo()\n"
    _write_fixture(tmp_path, source, {"total": 0, "files": {}})
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc != 0, f"Expected fail, got pass: {out}"
    assert "REFUSING to raise" in out or "FAIL" in out


def test_minus_one_passes(tmp_path):
    """Removing one occurrence should pass (decrease-only)."""
    source = f"import {_MP}\n{_MP}.foo()\n"
    baseline = {"total": 2, "files": {"src/rugbrain/test_monkey.py": 1, "src/rugbrain/other.py": 1}}
    _write_fixture(tmp_path, source, baseline)
    rc, out, _err = run_script([], repo=tmp_path)
    assert rc == 0, f"Expected pass (decrease allowed), got fail: {out} {_err}"
    assert "PASS" in out


def test_update_baseline_refuses_to_raise(tmp_path):
    """--update-baseline must refuse to raise the decrease-only budget."""
    source = f"import {_MP}\n{_MP}.foo()\n"
    _write_fixture(tmp_path, source, {"total": 0, "files": {}})
    rc, out, _err = run_script(["--update-baseline"], repo=tmp_path)
    assert rc != 0, f"Expected refusal, got pass: {out}"
    assert "REFUSING to raise" in out


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
