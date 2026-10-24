"""Per-file scan for the static half of the dimension-drift gate."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rugbrain.dim_drift_classify import _classification, _prose_claim
from rugbrain.dim_drift_files import _iter_comments, _relative
from rugbrain.dim_drift_nodes import (
    _CALL_ENV,
    _dict_keys,
    _docstring_nodes,
    _index_env_defaults,
    _model_ref_of,
)
from rugbrain.dim_drift_shapes import (
    _dict_size_bindings,
    _is_arithmetic_operand,
    _is_dim_shaped,
    _is_model_registry_value,
    _is_registry_named,
    _is_vector_construction,
    _resolve_slot,
    _vector_width_bindings,
)


def _scan_file(
    path: Path,
    base: Path,
    retired: frozenset[int],
    models: Mapping[str, int],
    skip_paths: set[Path],
) -> tuple[bool, str, list[Any], list[Any], list[Any], list[Any]]:
    """Scan one file: ``(scanned, rel, prose, declarations, failures, unparsed)``."""
    prose: list[tuple[str, int, str, str, bool]] = []
    declarations: list[tuple[str, int, str, str]] = []
    failures: list[tuple[str, int, str, str]] = []
    unparsed: list[tuple[str, str]] = []
    resolved = path.resolve()
    if resolved in skip_paths or "__pycache__" in resolved.parts:
        return False, "", [], [], [], []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return False, "", [], [], [], [(str(path), f"{type(exc).__name__}: {exc}")]
    rel = _relative(path, base)

    # Comments are invisible to the AST, so they are recovered from source
    # lines and checked separately. They can never fail the gate.
    for lineno, text in _iter_comments(source):
        claim = _prose_claim(text, retired, models)
        if claim:
            prose.append((rel, lineno, "MISLEADING_PROSE", claim, False))

    docstrings = _docstring_nodes(tree)
    dict_keys = _dict_keys(tree)
    _index_env_defaults(tree)
    width_names = _vector_width_bindings(tree) | _dict_size_bindings(tree)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        is_key = id(node) in dict_keys
        is_env = id(node) in _CALL_ENV
        if isinstance(value, str):
            if id(node) in docstrings:
                claim = _prose_claim(value, retired, models)
                if claim:
                    prose.append((rel, node.lineno, "MISLEADING_PROSE", claim, True))
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        if value not in retired:
            continue
        kind, slot = _resolve_slot(node, parents)
        cls = _classification(
            slot_kind=kind,
            slot_name=slot,
            value_repr=repr(value),
            retired=retired,
            models=models,
            is_dict_key=is_key or _is_model_registry_value(node, parents),
            is_env_default=is_env,
            is_vector_schema=_is_vector_construction(node, parents) or slot in width_names,
            is_arithmetic=_is_arithmetic_operand(node, parents),
        )
        where = "env-default" if is_env else f"{kind} {slot or '<anonymous>'!r}"
        record = (rel, node.lineno, cls, f"retired width {value} in {where}")
        if cls == "EXECUTABLE_DEFAULT":
            failures.append(record)
        else:
            declarations.append(record)

    # Retired WIDTH written as a STRING, e.g.
    #     EMBED_DIM = int(os.getenv("RAG_EMBED_DIM") or "2560")
    # This is not a stylistic detail, it is the exact defect the operator
    # asked to make impossible: `embed_lane.py` carried an 8B *model*
    # default beside a 2560 *dim* default, so the model said one thing and
    # the width another and whichever was read first won. An int-only scan
    # misses it completely, because the literal is a str. Only counted
    # when the slot is dim-shaped or it is an env fallback, so a prose
    # string that happens to contain "2560" is not a failure.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value.strip() not in {str(d) for d in retired}:
            continue
        if id(node) in docstrings or id(node) in dict_keys:
            continue
        is_env = id(node) in _CALL_ENV
        kind, slot = _resolve_slot(node, parents)
        # A registry-named binding stays a declaration in this pass too.
        # `PRESERVED_LANE_DIM = int(os.getenv(..., "2560"))` is env-gated
        # AND registry-named; without this the check fires forever on the
        # single line whose whole job is to name the retired width.
        if _is_registry_named(slot):
            declarations.append(
                (
                    rel,
                    node.lineno,
                    "REGISTRY_DECLARATION",
                    f"retired width {node.value.strip()} as a string in {kind} {slot!r}",
                )
            )
            continue
        if not (is_env or _is_dim_shaped(slot) or slot in width_names):
            continue
        where = "env-default" if is_env else f"{kind} {slot or '<anonymous>'!r}"
        failures.append(
            (
                rel,
                node.lineno,
                "EXECUTABLE_DEFAULT",
                f"retired width {node.value.strip()} as a string in {where}",
            )
        )

    # Retired MODEL names in string constants, same slot classification.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if _model_ref_of(node.value, models) is None:
            continue
        is_doc = id(node) in docstrings
        is_key = id(node) in dict_keys
        is_env = id(node) in _CALL_ENV
        if is_doc:
            prose.append(
                (
                    rel,
                    node.lineno,
                    "MISLEADING_PROSE",
                    "docstring names a retired model as if current",
                    True,
                )
            )
            continue
        if is_key:
            declarations.append(
                (
                    rel,
                    node.lineno,
                    "REGISTRY_DECLARATION",
                    f"dict key names the retired model {node.value.strip()[:60]!r}",
                )
            )
            continue
        kind, slot = _resolve_slot(node, parents)
        cls = _classification(
            slot_kind=kind,
            slot_name=slot,
            value_repr=node.value,
            retired=retired,
            models=models,
            is_env_default=is_env,
        )
        where = "env-default" if is_env else f"{kind} {slot or '<anonymous>'!r}"
        record = (
            rel,
            node.lineno,
            cls,
            f"retired model {node.value.strip()[:60]!r} in {where}",
        )
        if cls == "EXECUTABLE_DEFAULT":
            failures.append(record)
        else:
            declarations.append(record)

    return True, rel, prose, declarations, failures, unparsed
