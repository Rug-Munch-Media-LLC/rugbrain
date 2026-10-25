"""Repo-wide pytest hooks: rule-derived markers, no hand-maintained lists.

  live         module reaches the network (real http/socket client usage)
  network      explicit alias for live — a module states its own intent
  integration  module reads env vars or credentials
  slow         node id recorded above 1s in the baseline run (tests/_slow_nodes.json)
  unit         everything else (default bucket)

An explicit module-level ``pytestmark`` is AUTHORITATIVE: the AST guess never
overrides a declaration (``pytestmark = pytest.mark.network`` / ``.unit``).
Without a declaration the guess only fires on real client usage — a bare URL
string (env default, docs link, endpoint handed to a fake client) is NOT a
live signal.  That rule silently deselected 85 hermetic modules until
2026-10-25 (e.g. tests/test_embed_chain.py: 7 tests collected, 0 ever run).
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).parent
_SLOW_FILE = _ROOT / "tests" / "_slow_nodes.json"

_NET_MODULES = {
    "requests", "httpx", "aiohttp", "urllib", "urllib3",
    "socket", "websockets", "http", "pycurl", "grpc",
}
_ENV_ATTRS = {"environ", "getenv", "putenv", "setenv"}
_KNOWN_MARKS = {"live", "network", "integration", "slow", "unit"}
_ALIASES = {"network": "live"}

_cache: dict[str, set[str]] = {}


def _signals(tree: ast.AST) -> set[str]:
    """Names/attributes that reveal network or env access."""
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            seen.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, ast.Name):
            seen.add(node.id)
    return seen


def _mark_names(node: ast.AST) -> set[str]:
    """Mark names in a ``pytestmark`` value (single mark or list of marks)."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Attribute):
            if sub.value.attr == "mark":
                names.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if sub.value in _KNOWN_MARKS:
                names.add(sub.value)
    return names


def _declared(tree: ast.AST) -> set[str] | None:
    """Marks the module declares itself, or None when it declares nothing."""
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "pytestmark" in targets:
                return _mark_names(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "pytestmark" and node.value is not None:
                return _mark_names(node.value)
    return None


def classify(path: pathlib.Path) -> set[str]:
    """Return the marker set a test module earns: declaration first, guess second."""
    key = str(path)
    if key not in _cache:
        try:
            tree: ast.AST | None = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            tree = None
        declared = _declared(tree) if tree is not None else None
        if declared:
            tags = {_ALIASES.get(mark, mark) for mark in declared}
        elif tree is not None:
            seen = _signals(tree)
            tags = set()
            if seen & _NET_MODULES:
                tags.add("live")
            if seen & _ENV_ATTRS:
                tags.add("integration")
        else:
            tags = set()
        _cache[key] = tags
    return set(_cache[key])


def _slow_nodes() -> set[str]:
    try:
        return set(json.loads(_SLOW_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


@pytest.hookimpl(tryfirst=True)  # before the builtin -m deselection hook
def pytest_collection_modifyitems(config, items):
    slow = _slow_nodes()
    for item in items:
        path = pathlib.Path(str(item.path))
        tags = classify(path)
        if item.get_closest_marker("network"):
            tags.add("live")
        if item.nodeid.split("[")[0] in slow:
            tags.add("slow")
        if not tags & {"live", "integration"}:
            tags.add("unit")
        for tag in sorted(tags):
            item.add_marker(getattr(pytest.mark, tag))
