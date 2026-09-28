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

This check reads the *module* — the mappers and the ``DEFAULT_COLLECTION``
constants that build each document — and asserts that the collections a source
can actually emit are all reachable through its declared contract. A source
that routes to a collection it does not declare is a violation, and it is
reported by name.

It is read-only: modules are imported, never fetched, and nothing is written.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOURCES_DIR = Path(__file__).resolve().parents[1] / "ingest_sources"


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


def _constant_strings(tree: ast.AST) -> set[str]:
    """Every string literal assigned to a module-level or class-level name.

    Static rather than dynamic on purpose: the contract we care about is what
    the source *declares*, and reading the AST is deterministic and needs no
    network. A source that computes its collection from a live API response
    cannot be checked this way, and is reported as unverifiable rather than
    assumed compliant.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name) or (
                        isinstance(target, ast.Attribute) and target.attr == "default_collection"
                    ):
                        found.add(node.value.value)
    return found


def _module_constants(tree: ast.AST) -> dict[str, str]:
    """Map module-level ``NAME = "literal"`` assignments to their values."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        out[target.id] = node.value.value
    return out


def _collection_literals(tree: ast.AST) -> set[str]:
    """Collections this module can stamp into a document.

    The behavioural side of the contract: a mapper that stamps
    ``{"collection": X}`` decides where the document is written.

    ``X`` is resolved through module-level constants, not just read as a
    literal. A source that writes ``{"collection": SCAM_COLLECTION}`` routes
    exactly as hard as one that writes the string inline, and a check that
    only matched inline literals would wave it through — which is precisely
    how ``scythe_radar_datasets`` survived the per-source check.
    """
    constants = _module_constants(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "collection"):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(value.value)
            elif isinstance(value, ast.Name) and value.id in constants:
                found.add(constants[value.id])
    return found


def _declared_collections(tree: ast.AST) -> set[str]:
    """Every collection declared by a ``Source`` subclass in this module.

    A module can register several sources with different targets (a shared RSS
    base class, a multi-source module), so the contract is a SET of declared
    collections rather than a single one. Comparing against only the first
    class would report every sibling source as a violation.

    A ``default_collection`` implemented as a ``@property`` is deliberately
    not resolved: it is computed per-instance, so a static read cannot know
    its value. Such a module is treated as declaring nothing rather than
    guessed at.
    """
    constants = _module_constants(tree)
    declared: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(
                isinstance(t, ast.Name) and t.id == "default_collection" for t in stmt.targets
            ):
                continue
            value = stmt.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                declared.add(value.value)
            elif isinstance(value, ast.Name) and value.id in constants:
                declared.add(constants[value.id])
    return declared


def _declared_collection(tree: ast.AST) -> str | None:
    """Single declared collection, for callers that want one value."""
    found = _declared_collections(tree)
    return sorted(found)[0] if len(found) == 1 else (sorted(found)[0] if found else None)


def _defines_source_class(tree: ast.AST) -> bool:
    """True when the module defines a class with a string ``name`` attribute.

    A cheap structural proxy for "this module registers an ingest source",
    which is how helper modules are told apart from real sources.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _source_name(node) is not None:
            return True
    return False


def _source_name(tree: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "name" for t in stmt.targets)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    return stmt.value.value
    return None


def scan_module(path: Path) -> Violation | None:
    """Check one ingest-source module. Returns a Violation or None.

    A module is clean when it declares no collection, or when every collection
    literal it can stamp into a document is its declared
    ``default_collection``.

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

    if not _defines_source_class(tree):
        return None

    declared = _declared_collections(tree)
    if not declared:
        return None

    emitted = _collection_literals(tree)
    if not emitted:
        return None

    # A module may register several sources (a shared RSS base, a
    # multi-source module), each with its own declaration. A collection is
    # undeclared only when NO class in the module claims it.
    unknown = {c for c in emitted if c not in declared}
    if not unknown:
        return None

    return Violation(
        source=_source_name(tree) or path.stem,
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
        if path.name in {"__init__.py", "base.py"}:
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
