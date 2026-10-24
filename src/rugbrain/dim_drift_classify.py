"""Slot -> finding-class rules for the dimension-drift gate."""

from __future__ import annotations

from collections.abc import Mapping

from rugbrain.dim_drift_nodes import _model_ref_of
from rugbrain.dim_drift_registry import (
    _MANIFEST_FIELD_NAMES,
    _STALE_PROSE_TOKENS,
)
from rugbrain.dim_drift_shapes import _is_dim_shaped, _is_registry_named


def _classification(
    *,
    slot_kind: str,
    slot_name: str,
    value_repr: str,
    retired: frozenset[int],
    models: Mapping[str, int],
    is_dict_key: bool = False,
    is_env_default: bool = False,
    is_vector_schema: bool = False,
    is_arithmetic: bool = False,
) -> str:
    """Sort one hit into exactly one class. This is the whole contract.

    Order matters and is deliberate:

    1. A dict KEY naming a retired model is a declaration — this is the shape
       the canonical registry itself uses (``MODEL_DIMS``), and exempting it by
       filename would be the exemption that rots.
    2. A registry-named binding (``PRESERVED_LANE_DIM``, ``retired_dims``) is
       a declaration no matter what it holds — that is what makes a
       declaration a declaration.
    3. A manifest field is a record, not a choice.
    4. A retired *model name* in any other real slot is an executable default.
       There is no legitimate "record which model we used" manifest field, and
       a model default is the DeepInfra 200-OK trap.
    5. A retired *width* only counts as a default when the slot is dim-shaped
       or it is an ``os.getenv`` fallback. This is what stops ``1024`` chunk
       sizes and batch limits — which appear all over the backup and
       observability code — from being reported as embedding drift.
    """
    if is_dict_key:
        return "REGISTRY_DECLARATION"
    if _is_registry_named(slot_name):
        return "REGISTRY_DECLARATION"
    if is_arithmetic:
        return "MANIFEST_RECORD"
    if slot_kind == "field" and slot_name in _MANIFEST_FIELD_NAMES:
        return "MANIFEST_RECORD"
    if _model_ref_of(value_repr.strip("'\""), models) is not None:
        return "EXECUTABLE_DEFAULT"
    if slot_kind == "field":
        # A retired width in a field we do not recognise as a manifest field:
        # only a default when the field is dim-shaped, it is an env fallback,
        # or it IS the width of a vector schema. The vector arm is load-bearing
        # for `VectorParams(size=2560)`, whose keyword is literally called
        # "size" and carries no embedding hint — the constructor is the only
        # evidence that it is a width.
        if _is_dim_shaped(slot_name) or is_env_default or is_vector_schema:
            return "EXECUTABLE_DEFAULT"
        return "MANIFEST_RECORD"
    if _is_dim_shaped(slot_name) or is_env_default or is_vector_schema:
        return "EXECUTABLE_DEFAULT"
    return "MANIFEST_RECORD"


def _prose_claim(text: str, retired: frozenset[int], models: Mapping[str, int]) -> str | None:
    """Describe a stale prose claim about the retired generation, or None.

    False PROSE is not a failure — a stale comment breaks nothing at runtime.
    A docstring that asserts something untrue is worse, because a docstring
    is contract, so both are reported but flagged by kind.
    """
    low = text.lower()
    hits_retired = any(str(d) in low for d in retired) or any(name in low for name in models)
    if not hits_retired:
        return None
    claims = [token for token in _STALE_PROSE_TOKENS if token in low]
    if not claims:
        return None
    marker = ", ".join(repr(c) for c in claims[:3])
    return f"asserts the retired generation is current ({marker}); text is false prose"
