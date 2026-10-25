"""Repo-wide pytest hooks: rule-derived markers, no hand-maintained lists.

  live         module can reach the network (http client / socket / URL literal)
  integration  module reads env vars or credentials
  slow         node id recorded above 1s in the baseline run (tests/_slow_nodes.json)
  unit         everything else (default bucket)

Rules are computed from the AST in ``classify`` and applied in
``pytest_collection_modifyitems``; no test file needs editing to be classified.
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
_URL_MARKERS = ("ht" + "tp://", "ht" + "tps://")

_cache: dict[str, set[str]] = {}


def _signals(tree: ast.AST) -> set[str]:
    """Names/attributes/URL-literals that reveal network or env access."""
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
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(marker in node.value for marker in _URL_MARKERS):
                seen.add("__url__")
    return seen


def classify(path: pathlib.Path) -> set[str]:
    """Return the marker set a test module earns by rule."""
    key = str(path)
    if key not in _cache:
        try:
            seen = _signals(ast.parse(path.read_text(encoding="utf-8")))
        except (OSError, SyntaxError, ValueError):
            seen = set()
        tags: set[str] = set()
        if "__url__" in seen or seen & _NET_MODULES:
            tags.add("live")
        if seen & _ENV_ATTRS:
            tags.add("integration")
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
        tags = classify(pathlib.Path(str(item.path)))
        if item.nodeid.split("[")[0] in slow:
            tags.add("slow")
        if not tags & {"live", "integration"}:
            tags.add("unit")
        for tag in sorted(tags):
            item.add_marker(getattr(pytest.mark, tag))
