"""AST node helpers for the static half of the dimension-drift gate."""

from __future__ import annotations

import ast
from collections.abc import Mapping


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids() of the Constant nodes that are module/class/function docstrings."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))
    return found


def _target_name(node: ast.AST) -> str:
    """The dotted/last name a value is being bound to, lowercased."""
    if isinstance(node, ast.Name):
        return node.id.lower()
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    if isinstance(node, ast.keyword) and node.arg:
        return node.arg.lower()
    return ""


def _dict_keys(tree: ast.AST) -> set[int]:
    """ids() of Constant nodes used as a DICT KEY.

    ``MODEL_DIMS = {"qwen3-embedding-4b": 2560}`` is a declaration: the key
    *names* the retired generation. That is the single most important
    exception in the whole classifier, because the canonical registry itself
    lives in exactly this shape and a check that flags it fires forever on
    correct code. Detecting it structurally (is this node a dict key?) keeps
    the rule honest for every future registry file, instead of exempting one
    filename.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found.add(id(key))
    return found


def _model_ref_of(text: str, models: Mapping[str, int]) -> str | None:
    """Return the retired model this string IS, or None if it merely mentions one.

    A value is a model reference only when the whole string is the model name,
    or a ``provider/model`` path ending in it — the exact shape a client sends
    to an embeddings endpoint. A string that merely *contains* the name is not
    a reference: a HuggingFace cache path
    (``.../models--Qwen--Qwen3-Embedding-4B/snapshots/...``), a dataset card
    ("Training target: Qwen3-Embedding-4B fine-tuned"), and an argparse
    ``--help`` description all name the model without configuring anything.
    Flagging those would be a false positive on a check that must not fire
    forever, and they are handled on the prose track instead.
    """
    normalized = text.strip().lower()
    for name in models:
        low = name.lower()
        if normalized == low or normalized.endswith("/" + low):
            return name
    return None


def _is_env_default_slot() -> bool:
    """Deprecated shim retained for readability; env-defaults are indexed."""
    return False


_CALL_ENV: dict[int, bool] = {}


def _index_env_defaults(tree: ast.AST) -> None:
    """Record the ids of literals used as a configuration FALLBACK.

    Reset per file: the id() space is per-object, so a stale entry could only
    ever be a false negative, never a false positive — but clearing keeps the
    classifier honest when a file is scanned in isolation.

    Two shapes count as a fallback, and the second one is the important one:

    1. ``os.getenv("RAG_EMBED_DIM", "2560")`` — an explicit default argument.
    2. ``int(os.getenv("RAG_EMBED_DIM") or "2560")`` — a fallback ``or``-chain.

    Shape 2 is how the real defect in this repo was written::

        EMBED_DIM = int(os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or "2560")

    with ``QWEN_EMBED_MODEL_NAME`` already set to 8B three lines above. An
    int-only scan misses it because the literal is a ``str``; a str-only scan
    that ignores the ``or`` chain misses it because the literal is not a
    ``getenv`` argument. Both are needed for the gate to see the exact bug the
    operator asked to make impossible.
    """
    _CALL_ENV.clear()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name == "getenv" and len(node.args) >= 2:
            for arg in node.args[1:]:
                for inner in ast.walk(arg):
                    if isinstance(inner, ast.Constant):
                        _CALL_ENV[id(inner)] = True
        # Fallback chain: every non-Call operand of an `or` is a default.
        if not isinstance(node.func, ast.Name) or node.func.id != "int":
            continue
        for arg in node.args:
            _mark_fallback_operands(arg)


def _mark_fallback_operands(node: ast.AST) -> None:
    """Mark literals in an ``or``-chain that are not themselves ``getenv`` calls."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        for value in node.values:
            if isinstance(value, ast.Call):
                continue  # the env lookup itself, not a fallback
            _mark_fallback_operands(value)
        return
    for inner in ast.walk(node):
        if isinstance(inner, ast.Constant):
            _CALL_ENV[id(inner)] = True


def _slot_of(node: ast.Constant, parent: ast.AST | None) -> tuple[str, str]:
    """Return ``(slot_kind, slot_name)`` for a constant's position.

    ``slot_kind`` is ``field`` (a keyword arg — a manifest record), ``name``
    (a module/class/function attribute name), or ``bare`` (an anonymous
    literal). The kind is what separates "recorded a measurement" from
    "chose a default".

    The parent may legitimately be a CALL rather than the assignment — in
    ``PRESERVED_LANE_DIM = int(os.getenv("X", "2560"))`` the literal's direct
    parent is the ``getenv`` call. Resolving only the direct parent would lose
    the binding name and misclassify a declaration as an anonymous default, so
    the caller walks the ancestor chain; this stays the single-step rule.
    """
    if isinstance(parent, ast.keyword):
        return "field", _target_name(parent)
    if isinstance(parent, ast.Assign | ast.AnnAssign | ast.AugAssign):
        targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
        if targets:
            return "name", _target_name(targets[0])
    return "bare", ""
