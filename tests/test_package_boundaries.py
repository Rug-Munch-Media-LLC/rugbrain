"""Enforceable package-boundary contracts for rugbrain (see ``docs/STRUCTURE.md``).

Rule 1 — nothing under ``src/rugbrain`` may import the ``scripts`` tree.
         ``scripts/`` is a loose, untyped application layer; the library must
         stay installable and testable without it.
Rule 2 — ``rugbrain.knowledge`` is a pure-data leaf: it may import only its own
         siblings (plus the standard library). No I/O, no service, no client.

Structural tests, not review guidelines: a violation fails CI. The matching
import-linter contract for rule 1 lives in ``.importlinter``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "src" / "rugbrain"
KNOWLEDGE_ROOT = PKG_ROOT / "knowledge"


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_modules(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # unreadable file cannot import anything
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
    return mods


def find_scripts_imports(pkg_root: Path = PKG_ROOT, repo_root: Path = REPO_ROOT) -> list[str]:
    """Return every ``src/rugbrain`` module that imports ``scripts``."""
    violations: list[str] = []
    for path in _py_files(pkg_root):
        if "scripts" not in path.read_text(encoding="utf-8"):  # cheap prefilter
            continue
        for mod in sorted(_imported_modules(path)):
            if mod == "scripts" or mod.startswith("scripts."):
                violations.append(f"{path.relative_to(repo_root)} imports {mod}")
    return violations


def find_knowledge_leaks(
    knowledge_root: Path = KNOWLEDGE_ROOT, repo_root: Path = REPO_ROOT
) -> list[str]:
    """Return every import in ``rugbrain.knowledge`` that leaves the leaf."""
    stdlib = set(sys.stdlib_module_names)
    violations: list[str] = []
    for path in _py_files(knowledge_root):
        for mod in sorted(_imported_modules(path)):
            if mod.startswith("rugbrain"):
                if not (mod == "rugbrain.knowledge" or mod.startswith("rugbrain.knowledge.")):
                    violations.append(f"{path.relative_to(repo_root)} imports {mod}")
            elif mod.split(".")[0] not in stdlib:
                violations.append(f"{path.relative_to(repo_root)} imports non-stdlib {mod}")
    return violations


def test_library_never_imports_scripts() -> None:
    violations = find_scripts_imports()
    assert not violations, "scripts/ imported from the library:\n" + "\n".join(violations)


def test_knowledge_is_a_pure_data_leaf() -> None:
    violations = find_knowledge_leaks()
    assert not violations, "rugbrain.knowledge must stay dependency-free:\n" + "\n".join(violations)


if __name__ == "__main__":  # runnable without pytest
    test_library_never_imports_scripts()
    test_knowledge_is_a_pure_data_leaf()
    print("boundary contracts hold")
