"""Value-shape classification helpers for the dimension-drift gate."""

from __future__ import annotations

import ast
from collections.abc import Mapping

from rugbrain.dim_drift_nodes import _slot_of
from rugbrain.dim_drift_registry import (
    _DIM_NAME_TOKENS,
    _REGISTRY_NAME_TOKENS,
    MODEL_DIMS,
)


def _is_model_registry_value(node: ast.Constant, parents: Mapping[int, ast.AST]) -> bool:
    """True when the constant is a VALUE in a dict keyed by model names.

    ``MODEL_DIMS = {"qwen3-embedding-8b": 4096, "qwen3-embedding-4b": 2560}``
    — the retired model is the KEY and the retired width is the paired VALUE.
    That pairing is the declaration: it is how a registry says "this model
    produces this width". Detecting it structurally (are the sibling keys model
    names?) covers every registry file, present and future, instead of
    exempting ``dim_policy.py`` by name.

    A plain width table like ``PINNED_DIMS = {"wallet_clusters": 1024}`` has
    collection names as keys, not model names, so it is not matched here — and
    it does not need to be, because those values are already classified as
    manifest records by the field rule.
    """
    parent = parents.get(id(node))
    if not isinstance(parent, ast.Dict):
        return False
    for key in parent.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            lowered = key.value.strip().lower()
            if any(lowered == n.lower() or lowered.endswith("/" + n.lower()) for n in MODEL_DIMS):
                return True
    return False


def _vector_width_bindings(tree: ast.AST) -> set[str]:
    """Names bound to a Qdrant vector width anywhere in the file.

    Dataflow, not shape: this repo writes vector widths in two steps —

        size = vector_size or 2560
        client.create_collection(..., vectors_config=VectorParams(size=size, ...))

    — so the literal is nowhere near the constructor and no amount of
    structural proximity will connect them. The reliable signal is the
    *consuming call*: a value passed as ``size=`` to a vector-params
    constructor, or read out of a ``{"size": ...}`` literal, is a vector
    width, and whatever local name holds it is a width too. Collecting those
    names first turns a scattered two-line idiom into a precise rule, and is
    why dropping "size" from the name heuristics did not lose these.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if fname not in {"VectorParams", "SparseVectorParams", "DenseVectorParams"}:
            continue
        for keyword in node.keywords:
            if keyword.arg == "size" and isinstance(keyword.value, ast.Name):
                names.add(keyword.value.id.lower())
    return names


def _dict_size_bindings(tree: ast.AST) -> set[str]:
    """Names read out of a vector-schema dict literal's ``size`` key."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and key.value == "size"
                and isinstance(value, ast.Name)
            ):
                names.add(value.id.lower())
    return names


def _is_arithmetic_operand(node: ast.Constant, parents: Mapping[int, ast.AST]) -> bool:
    """True when the literal is a factor in arithmetic, not a standalone value.

    ``5 * 1024 * 1024`` and ``4 * 1024 * 1024`` are byte sizes in the backup
    paths. The literal is an *operand of a multiplication* — a
    unit conversion — and can never be an embedding width. Without this rule
    the gate reports four byte-size bugs that are not bugs, and a gate that
    cries wolf on a backup script is a gate that gets muted.
    """
    current: ast.AST | None = parents.get(id(node))
    for _ in range(3):
        if current is None:
            return False
        if isinstance(current, ast.BinOp):
            return True
        current = parents.get(id(current))
    return False


def _is_vector_construction(node: ast.Constant, parents: Mapping[int, ast.AST]) -> bool:
    """True when the literal is the width of a Qdrant vector schema.

    ``VectorParams(size=2560, ...)`` and ``{"size": 2560, "distance": "Cosine"}``
    both declare a collection's width directly, and the ``size`` key carries
    no embedding hint at all. Matching the constructor structurally is what
    lets "size" be dropped from the name heuristics without losing these —
    the two most dangerous widths in the tree, since they create the
    non-canonical collection the live half then refuses.
    """
    current: ast.AST | None = parents.get(id(node))
    for _ in range(3):
        if current is None:
            return False
        if isinstance(current, ast.Dict):
            for key, value in zip(current.keys, current.values, strict=False):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "size"
                    and not isinstance(value, (ast.Name, ast.Call, ast.Subscript))
                ):
                    return True
        if isinstance(current, ast.Call):
            func = current.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"VectorParams", "SparseVectorParams", "DenseVectorParams"}:
                return True
        current = parents.get(id(current))
    return False


def _resolve_slot(node: ast.Constant, parents: Mapping[int, ast.AST]) -> tuple[str, str]:
    """Nearest enclosing name/field for *node*, walking past call wrappers.

    ``PRESERVED_LANE_DIM = int(os.getenv("RAG_PRESERVED_LANE_DIM", "2560"))``
    has the literal two levels below the ``AnnAssign``. Stopping at the first
    ancestor that names anything keeps the registry's own declarations
    correctly classified, which is the difference between a check that can be
    trusted and one that has to be muted.
    """
    current: ast.AST | None = parents.get(id(node))
    for _ in range(4):  # bounded: a name is never more than a call or two away
        if current is None:
            break
        kind, name = _slot_of(node, current)
        if kind != "bare":
            return kind, name
        current = parents.get(id(current))
    return "bare", ""


def _is_dim_shaped(name: str) -> bool:
    return any(token in name for token in _DIM_NAME_TOKENS)


def _is_registry_named(name: str) -> bool:
    return any(token in name for token in _REGISTRY_NAME_TOKENS)
