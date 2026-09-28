"""One-module-one-task audit for the ingest-source registry.

The operator rule this file enforces:

    A source module writes to exactly ONE collection, does one job, and
    never writes on another source's behalf. ``default_collection`` stays
    single-valued.

Two declarations of the same fact used to drift silently (a source's
``default_collection`` vs ``qdrant_store.collections._RMI_RAG_OWNED_COLLECTIONS``,
and again vs what actually exists on Qdrant). Three sources once reported
success while writing into a void. This module is the check that would
have caught it: it resolves the registry, validates the declarations it
owns, and reports the one fact it cannot own (Qdrant existence) as data.

Run::

    python -m rugbrain.ingest_sources.registry_contract          # human
    python -m rugbrain.ingest_sources.registry_contract --json   # machine

Exit code 0 = every source declares exactly one owned collection.
Exit code 1 = at least one violation (see ``violations``).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any

# Modules allowed to hold write primitives. Everything else must produce
# ``IngestDoc``s and nothing else — a source that embeds or upserts is doing
# the storage layer's job, which is how one module starts writing for another.
_WRITER_MODULES = frozenset(
    {
        "telegram_userbot",
        "telegram_web",
        "x_intel.storage",
    }
)

# Directories that are not sources: shared helpers, orchestration, Telegram
# transport, taxonomy vocabularies, and the document pipeline itself.
_NON_SOURCE_PREFIXES = ("channel_", "ingest_", "sweep_", "tg_")

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


@dataclass
class Report:
    """Result of one audit pass."""

    sources: int = 0
    distinct_collections: int = 0
    owned: set[str] = field(default_factory=set)
    live: set[str] | None = None
    source_targets: dict[str, str] = field(default_factory=dict)
    collection_uses: list[CollectionUse] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources": self.sources,
            "distinct_collections": self.distinct_collections,
            "owned_count": len(self.owned),
            "live_count": None if self.live is None else len(self.live),
            "source_targets": self.source_targets,
            "collection_uses": [vars(u) for u in self.collection_uses],
            "writers": self.writers,
            "violations": self.violations,
        }


def owned_collections() -> set[str]:
    """Read the canonical owned-set. Imported lazily: this is a registry module."""
    from rugbrain.qdrant_store.collections import _RMI_RAG_OWNED_COLLECTIONS

    return set(_RMI_RAG_OWNED_COLLECTIONS)


def live_collections(qdrant_url: str = "http://localhost:6334") -> set[str]:
    """GET ``/collections`` from Qdrant. Read-only; empty set when unreachable.

    Never writes: this is a GET, and the registry must stay safe to run from
    any cron. An unreachable Qdrant is reported as an empty live set and the
    caller treats existence as "unknown", not as a failure.
    """
    import urllib.request

    if not qdrant_url.startswith("http://") and not qdrant_url.startswith("https://"):
        return set()
    try:
        with urllib.request.urlopen(  # noqa: S310 — scheme validated above
            f"{qdrant_url}/collections", timeout=10
        ) as resp:
            payload = json.loads(resp.read())
    except Exception:
        return set()
    return {str(c.get("name", "")) for c in payload.get("result", {}).get("collections", [])}


def _source_modules() -> dict[str, Any]:
    """``{module_name: module_object}`` for every module holding a Source."""
    import sys

    from rugbrain.ingest_sources import SOURCES

    out: dict[str, Any] = {}
    for src in SOURCES:
        mod = type(src).__module__
        out[mod.rsplit(".", 1)[-1]] = sys.modules[mod]
    return out


def _read(path: str | None) -> str:
    """Read a module's source text; "" when the path is missing or unreadable."""
    if not path:
        return ""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def scan_collection_uses(modules: dict[str, Any]) -> list[CollectionUse]:
    """Every ``metadata["collection"]`` literal each source module can emit.

    This is where a source admits a SECOND target: the default plus a branch
    that names a different collection. A single-valued default alone is not
    enough — the branch is the silent one.
    """
    uses: list[CollectionUse] = []
    for name, mod in modules.items():
        for i, line in enumerate(_read(mod.__file__).splitlines(), 1):
            for match in _METADATA_COLLECTION_RE.finditer(line):
                expr = match.group(1).strip().strip("\"'")
                uses.append(CollectionUse(name, i, expr, _resolve(mod, expr)))
    return uses


def _resolve(mod: Any, expr: str) -> str:
    """Resolve a ``metadata["collection"]`` expression inside its OWN module.

    Resolution is deliberately module-scoped: a shared constant name means
    two different things in two modules (``DEFAULT_COLLECTION`` is
    ``defi_exploits`` in ``incidents`` and ``crime_reports`` in
    ``chainabuse``), so resolving against the package would have made every
    check pass and missed ``scythe_radar_datasets``' second target entirely.
    """
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
    return _module_const(mod, expr)


def _module_const(mod: Any, name: str) -> str:
    """Module-level ``NAME = "literal"`` lookup, for constants AST missed."""
    import ast

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


def scan_writers(modules: dict[str, Any]) -> list[str]:
    """Modules that call a write primitive instead of only producing docs."""
    found: list[str] = []
    for name, mod in modules.items():
        if name in _WRITER_MODULES:
            continue
        if _WRITE_PRIMITIVE_RE.search(_read(mod.__file__)):
            found.append(name)
    return sorted(found)


def audit(*, qdrant_url: str | None = "http://localhost:6334") -> Report:
    """Run every check and return the report. Pure read; never mutates state."""
    from rugbrain.ingest_sources import SOURCES

    rep = Report(sources=len(SOURCES))
    rep.owned = owned_collections()
    rep.live = live_collections(qdrant_url) if qdrant_url else None

    targets: dict[str, set[str]] = {}
    for src in SOURCES:
        declared = str(getattr(src, "default_collection", "") or "")
        rep.source_targets[src.name] = declared
        targets.setdefault(declared, set()).add(src.name)

    rep.distinct_collections = len([c for c in targets if c])
    modules = _source_modules()
    rep.collection_uses = scan_collection_uses(modules)
    rep.writers = scan_writers(modules)

    # 1. single-valued default
    for name, declared in rep.source_targets.items():
        if not declared:
            rep.violations.append(f"{name}: empty default_collection")
        elif declared not in rep.owned:
            rep.violations.append(f"{name}: default_collection {declared!r} not in owned-set")

    # 2. no second target: every collection a module can emit must be one of
    #    the collections its own registered sources declare. A module holding
    #    two sources (incidents, bridges, zk) keeps one collection each.
    declared_by: dict[str, set[str]] = {}
    for src in SOURCES:
        mod = type(src).__module__.rsplit(".", 1)[-1]
        declared_by.setdefault(mod, set()).add(str(getattr(src, "default_collection", "")))
    for use in rep.collection_uses:
        allowed = declared_by.get(use.module, set())
        if use.resolved and use.resolved not in allowed:
            rep.violations.append(
                f"{use.module}:{use.line}: emits {use.resolved!r} but declares {sorted(allowed)}"
            )

    # 3. writers
    for mod in rep.writers:
        rep.violations.append(f"{mod}: calls a write primitive (embed/upsert) inside a source")

    # 4. existence (informational when Qdrant is unreachable)
    if rep.live:
        for coll in sorted(targets):
            if coll and coll not in rep.live:
                rep.violations.append(f"collection {coll!r} declared but absent on Qdrant")
    return rep


def main() -> int:
    parser = argparse.ArgumentParser(description="audit ingest-source registry contracts")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--qdrant", default="http://localhost:6334", help="Qdrant URL (read-only)")
    args = parser.parse_args()

    rep = audit(qdrant_url=args.qdrant)
    if args.json:
        print(json.dumps(rep.as_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"sources={rep.sources} distinct_collections={rep.distinct_collections} "
            f"owned={len(rep.owned)} live={len(rep.live or ())} writers={len(rep.writers)}"
        )
        for name, coll in sorted(rep.source_targets.items()):
            print(f"  {name:<34}{coll}")
        for line in rep.violations:
            print(f"VIOLATION {line}")
        print(
            "OK: one module, one collection"
            if not rep.violations
            else f"FAILED: {len(rep.violations)}"
        )
    return 1 if rep.violations else 0


__all__ = [
    "CollectionUse",
    "Report",
    "audit",
    "live_collections",
    "main",
    "owned_collections",
]


if __name__ == "__main__":
    raise SystemExit(main())
