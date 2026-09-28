"""Static scan of source module text: declared write primitives and targets.

Two things only text can answer, which is why this is separate from the audit
logic in ``registry_contract``:

  * does this module call a write primitive instead of only producing docs?
  * every ``metadata["collection"]`` value can it emit — including the branch
    that names a SECOND collection, which a single-valued default hides.

Constant resolution is deliberately module-scoped: a shared constant name
means two different things in two modules (``DEFAULT_COLLECTION`` is
``defi_exploits`` in ``incidents`` and ``crime_reports`` in ``chainabuse``), so
resolving against the package would have made every check pass and missed
``scythe_radar_datasets``' second target entirely.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import dataclass
from typing import Any

# Modules allowed to hold write primitives. Everything else must produce
# ``IngestDoc``s and nothing else — a source that embeds or upserts is doing
# the storage layer's job, which is how one module starts writing for another.
WRITER_MODULES = frozenset(
    {
        "telegram_userbot",
        "telegram_web",
        "x_intel.storage",
    }
)

_WRITE_PRIMITIVE_RE = re.compile(
    r"\b(?:upsert|store_documents|get_embedding|\.embed\(|qdrant_store)\b"
)
_METADATA_COLLECTION_RE = re.compile(r"""["']collection["']\s*:\s*([^,\n}]+)""")


@dataclass(frozen=True)
class CollectionUse:
    """One ``metadata["collection"]`` value a module can emit."""

    module: str
    line: int
    expression: str
    resolved: str


def read_text(path: str | None) -> str:
    """Read a module's source text; "" when the path is missing or unreadable."""
    if not path:
        return ""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def resolve(mod: Any, expr: str) -> str:
    """Resolve a ``metadata["collection"]`` expression inside its OWN module."""
    if not expr:
        return ""
    if expr[0] in "\"'":
        return expr.strip("\"'")
    if expr.startswith("self."):
        val = getattr(mod, expr.split(".", 1)[1], None)
        if isinstance(val, property):
            return ""
        if isinstance(val, str):
            return val
        return str(getattr(mod, "DEFAULT_COLLECTION", "") or "")
    if expr == "collection":
        return str(getattr(mod, "DEFAULT_COLLECTION", "") or "")
    val = getattr(mod, expr, None)
    if isinstance(val, str):
        return val
    return module_const(mod, expr)


def module_const(mod: Any, name: str) -> str:
    """Module-level ``NAME = "literal"`` lookup, for constants AST missed."""
    path = getattr(mod, "__file__", None)
    if not path:
        return ""
    try:
        tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return ""
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if any(getattr(t, "id", None) == name for t in targets):
            try:
                val = ast.literal_eval(node.value)
            except ValueError:
                return ""
            if isinstance(val, str):
                return val
    return ""


def scan_collection_uses(modules: dict[str, Any]) -> list[CollectionUse]:
    """Every ``metadata["collection"]`` literal each source module can emit.

    This is where a source admits a SECOND target: the default plus a branch
    that names a different collection. A single-valued default alone is not
    enough — the branch is the silent one.
    """
    uses: list[CollectionUse] = []
    for name, mod in modules.items():
        for i, line in enumerate(read_text(mod.__file__).splitlines(), 1):
            for match in _METADATA_COLLECTION_RE.finditer(line):
                expr = match.group(1).strip().strip("\"'")
                uses.append(CollectionUse(name, i, expr, resolve(mod, expr)))
    return uses


def scan_writers(modules: dict[str, Any]) -> list[str]:
    """Modules that call a write primitive instead of only producing docs."""
    found: list[str] = []
    for name, mod in modules.items():
        if name in WRITER_MODULES:
            continue
        if _WRITE_PRIMITIVE_RE.search(read_text(mod.__file__)):
            found.append(name)
    return sorted(found)


__all__ = [
    "WRITER_MODULES",
    "CollectionUse",
    "module_const",
    "read_text",
    "resolve",
    "scan_collection_uses",
    "scan_writers",
]
