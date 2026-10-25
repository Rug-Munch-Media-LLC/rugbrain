"""Regression: a clone's SUCCESS is its exit code, never its stdout.

git writes clone progress to stderr, so the old ``stdout`` heuristic discarded
every good clone and then ``rm -rf``'d it — all 5 protocols were recorded as
"no docs repo clonable". These tests use real local git repos; no mocks.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from defi_docs_fetch import clone  # noqa: E402


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "srcrepo"
    src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "LICENSE").write_text("MIT License\n\nPermission is hereby granted, free of charge\n")
    (src / "concepts" ).mkdir()
    (src / "concepts" / "pools.md").write_text("# Pools\n\nfee tier and tick spacing\n")
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    return src


def test_clone_succeeds_when_stdout_is_empty(tmp_path):
    """A local clone writes to stderr only — exactly the case the old code failed."""
    dest, attempts = (lambda r: (clone("proto", [f"file://{_source(tmp_path)}"], r)[0], r))(tmp_path / "out")
    assert dest is not None, "clone success must be returncode==0, not stdout"
    assert (dest / ".git").is_dir()
    assert (dest / "LICENSE").read_text().startswith("MIT")


def test_failed_clone_is_not_success(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    dest, attempts = clone("proto", ["file:///nonexistent/definitely-not-a-repo"], root)
    assert dest is None
    assert attempts and attempts[0]["rc"] != 0
    assert not (root / "proto" / ".git").exists()


def test_failed_tree_is_kept_for_inspection(tmp_path):
    """Never `rm -rf` the evidence: a pre-existing tree survives a failed clone."""
    root = tmp_path / "out"
    root.mkdir()
    stale = root / "proto"
    stale.mkdir()
    (stale / "marker").write_text("keep me")
    assert clone("proto", ["file:///nonexistent/a", "file:///nonexistent/b"], root)[0] is None
    assert any((path / "marker").exists() for path in root.glob("proto*"))
