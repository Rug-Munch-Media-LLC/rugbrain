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

The work is split by responsibility:

  * :mod:`registry_facts` — the owned set and Qdrant's live set (external facts)
  * :mod:`registry_scan` — static text scan of the source modules themselves
  * this module — the audit rules and the operator output

Run::

    python -m rugbrain.ingest_sources.registry_contract          # human
    python -m rugbrain.ingest_sources.registry_contract --json   # machine

Exit code 0 = every source declares exactly one owned collection.
Exit code 1 = at least one violation (see ``violations``).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Any

from rugbrain.ingest_sources.registry_facts import (
    live_collections,
    owned_collections,
    source_modules,
)
from rugbrain.ingest_sources.registry_scan import (
    CollectionUse,
    scan_collection_uses,
    scan_writers,
)


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
    modules = source_modules()
    rep.collection_uses = scan_collection_uses(modules)
    rep.writers = scan_writers(modules)

    _check_single_valued_defaults(rep)
    _check_no_second_target(rep)
    _check_no_writers(rep)
    _check_existence(rep, targets)
    return rep


def _check_single_valued_defaults(rep: Report) -> None:
    """Rule 1: every source declares exactly one owned collection."""
    for name, declared in rep.source_targets.items():
        if not declared:
            rep.violations.append(f"{name}: empty default_collection")
        elif declared not in rep.owned:
            rep.violations.append(f"{name}: default_collection {declared!r} not in owned-set")


def _check_no_second_target(rep: Report) -> None:
    """Rule 2: every collection a module can emit is one its own sources declare.

    A module holding two sources (incidents, bridges, zk) keeps one
    collection each.
    """
    from rugbrain.ingest_sources import SOURCES

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


def _check_no_writers(rep: Report) -> None:
    """Rule 3: no source module calls a write primitive."""
    for mod in rep.writers:
        rep.violations.append(f"{mod}: calls a write primitive (embed/upsert) inside a source")


def _check_existence(rep: Report, targets: dict[str, set[str]]) -> None:
    """Rule 4: declared collections exist on Qdrant (skipped when unreachable)."""
    if not rep.live:
        return
    for coll in sorted(targets):
        if coll and coll not in rep.live:
            rep.violations.append(f"collection {coll!r} declared but absent on Qdrant")


def _print_human(rep: Report) -> None:
    print(
        f"sources={rep.sources} distinct_collections={rep.distinct_collections} "
        f"owned={len(rep.owned)} live={len(rep.live or ())} writers={len(rep.writers)}"
    )
    for name, coll in sorted(rep.source_targets.items()):
        print(f"  {name:<34}{coll}")
    for line in rep.violations:
        print(f"VIOLATION {line}")
    print(
        "OK: one module, one collection" if not rep.violations else f"FAILED: {len(rep.violations)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="audit ingest-source registry contracts")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--qdrant", default="http://localhost:6334", help="Qdrant URL (read-only)")
    args = parser.parse_args()

    rep = audit(qdrant_url=args.qdrant)
    if args.json:
        print(json.dumps(rep.as_dict(), indent=2, sort_keys=True))
    else:
        _print_human(rep)
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
