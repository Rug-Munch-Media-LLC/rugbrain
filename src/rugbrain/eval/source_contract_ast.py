"""Static AST extractors for the ingest-source collection contract.

Split out of ``registry_contract`` so the READING of a module (what strings
does it declare, what collections can it stamp) is separate from the DECIDING
(does declaration cover emission, and is that a violation).

All of this is static, on purpose. The contract we care about is what a source
*declares* and what its mappers *can* emit, and reading the AST is
deterministic and needs no network. A source that computes its collection from
a live API response cannot be checked this way — such a module is reported as
declaring nothing rather than guessed at.
"""

from __future__ import annotations

import ast


def module_constants(tree: ast.AST) -> dict[str, str]:
    """Map module-level ``NAME = "literal"`` assignments to their values.

    Resolving names is what makes the check real. A source that writes
    ``{"collection": SCAM_COLLECTION}`` routes exactly as hard as one that
    writes the string inline, and an extractor that only matched inline
    literals would wave it through — which is precisely how
    ``scythe_radar_datasets`` survived the earlier per-source check.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def collection_literals(tree: ast.AST) -> set[str]:
    """Collections this module can stamp into a document.

    The behavioural side of the contract: a mapper that stamps
    ``{"collection": X}`` decides where the document is written.
    """
    constants = module_constants(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if not (isinstance(key, ast.Constant) and key.value == "collection"):
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(value.value)
            elif isinstance(value, ast.Name) and value.id in constants:
                found.add(constants[value.id])
    return found


def declared_collections(tree: ast.AST) -> set[str]:
    """Every collection declared by a ``Source`` subclass in this module.

    A module can register several sources with different targets (a shared RSS
    base class, a multi-source module), so the contract is a SET of declared
    collections rather than a single one. Comparing against only the first
    class would report every sibling source as a violation.

    A ``default_collection`` implemented as a ``@property`` is deliberately
    not resolved: it is computed per-instance, so a static read cannot know
    its value.
    """
    constants = module_constants(tree)
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


def source_name(tree: ast.AST) -> str | None:
    """The module's registered source name, if it declares one."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "name" for t in stmt.targets)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                return stmt.value.value
    return None


def defines_source_class(tree: ast.AST) -> bool:
    """True when the module defines a class with a string ``name`` attribute.

    A cheap structural proxy for "this module registers an ingest source",
    which is how helper modules are told apart from real sources.
    """
    return source_name(tree) is not None


__all__ = [
    "collection_literals",
    "declared_collections",
    "defines_source_class",
    "module_constants",
    "source_name",
]
