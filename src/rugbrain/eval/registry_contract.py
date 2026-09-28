"""Module-scoped registry contract check for the ingest sources.

Why module-scoped and not per-source
------------------------------------
A per-source check that only asserts ``Source.default_collection`` is a
non-empty string passed all 50 sources. It is a tautology: the class attribute
was set, so of course it is set. It could not detect that
``scythe_radar_datasets`` *declares* ``rug_events`` while the documents it
actually produces carry ``metadata["collection"] == "known_scams"`` — and
``scripts/ingest_all_sources.py`` routes on the metadata, not the declaration:

    _route_collection() -> metadata.get("collection") or default_collection

So the declaration is documentation, the metadata is behaviour, and a check on
the declaration alone is theatre.

This module scans the *module* — the mappers and the ``DEFAULT_COLLECTION``
constants that build each document — and asserts that the collections a source
can actually emit are all reachable through its declared contract. The AST
readers live in :mod:`rugbrain.eval.source_contract_ast`; this file owns the
DECISION and the reporting.

It is read-only: modules are parsed, never imported and never fetched, and
nothing is written.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rugbrain.eval.source_contract_ast import (
    collection_literals,
    declared_collections,
    defines_source_class,
    source_name,
)

SOURCES_DIR = Path(__file__).resolve().parents[1] / "ingest_sources"

#: Modules with no ingest contract of their own; skipping them by name keeps
#: the scan focused on sources rather than shared base/helper code.
_NON_SOURCE_FILES = frozenset({"__init__.py", "base.py"})


@dataclass(frozen=True)
class Violation:
    """One source whose emitted collections do not match its declaration."""

    source: str
    module: str
    declared: str
    emitted: tuple[str, ...]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "module": self.module,
            "declared": self.declared,
            "emitted": list(self.emitted),
            "detail": self.detail,
        }


def scan_module(path: Path) -> Violation | None:
    """Check one ingest-source module. Returns a Violation or None.

    A module is clean when it declares no collection, or when every collection
    it can stamp into a document is its declared ``default_collection``.

    A module that defines no ``Source`` subclass at all is NOT a violation: it
    is a helper (fetch_state, channel_taxonomy, corpus helpers) that declares
    no contract to breach. Reporting those as failures would bury the real
    signal — a check whose output is mostly noise gets ignored, which is how a
    true violation survives anyway.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return Violation(
            source=path.stem,
            module=path.name,
            declared="",
            emitted=(),
            detail=f"unreadable module: {exc}",
        )

    if not defines_source_class(tree):
        return None

    declared = declared_collections(tree)
    if not declared:
        return None

    emitted = collection_literals(tree)
    if not emitted:
        return None

    # A module may register several sources (a shared RSS base, a
    # multi-source module), each with its own declaration. A collection is
    # undeclared only when NO class in the module claims it.
    unknown = {c for c in emitted if c not in declared}
    if not unknown:
        return None

    return Violation(
        source=source_name(tree) or path.stem,
        module=path.name,
        declared=", ".join(sorted(declared)),
        emitted=tuple(sorted(unknown)),
        detail=(
            f"module can stamp documents into {sorted(unknown)} but no Source "
            f"in the module declares it (declared: {sorted(declared)}); ingest "
            f"routes on metadata['collection'], so the write target is undeclared"
        ),
    )


def scan_all(sources_dir: Path = SOURCES_DIR) -> list[Violation]:
    """Scan every ingest-source module. Modules with no Source class are skipped.

    A missing or empty directory is itself a failure. A gate that scans zero
    modules and exits 0 has proved nothing, which is the exact defect that let
    a wrong path read as "50 sources clean" — the check must never be able to
    pass by finding nothing.
    """
    if not sources_dir.is_dir():
        return [
            Violation(
                source="<registry>",
                module=str(sources_dir),
                declared="",
                emitted=(),
                detail=(
                    f"ingest_sources directory not found at {sources_dir}; "
                    "the contract check scanned nothing and cannot pass"
                ),
            )
        ]
    violations: list[Violation] = []
    for path in sorted(sources_dir.glob("*.py")):
        if path.name in _NON_SOURCE_FILES:
            continue
        found = scan_module(path)
        if found is not None:
            violations.append(found)
    return violations


def render(violations: list[Violation]) -> str:
    if not violations:
        return "registry contract OK: every ingest source routes to its declared collection"
    lines = [f"registry contract VIOLATIONS: {len(violations)}"]
    for v in violations:
        lines.append(f"  - {v.source} ({v.module})")
        lines.append(f"      declared={v.declared!r} emitted={list(v.emitted)}")
        lines.append(f"      {v.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Exit 0 clean, 1 on any violation. Read-only; no network, no writes."""
    import argparse

    p = argparse.ArgumentParser(prog="rugbrain.eval.registry_contract")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--sources-dir", type=Path, default=SOURCES_DIR)
    args = p.parse_args(argv)

    violations = scan_all(args.sources_dir)
    if args.json:
        print(json.dumps({"violations": [v.as_dict() for v in violations]}, indent=2))
    else:
        print(render(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
