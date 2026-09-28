"""Relabelling a canary hit so the shadow target name never leaves the boundary.

One job: take a sanitised result and rewrite every reference to the ``_8b``
shadow collection back to the public source collection. Split out from the
config gate and the embedder because a route that only needs to *hide* the
shadow name should not have to be able to enable the lane or call the provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rugbrain.canary_config import CANARY_SUFFIX
from rugbrain.knowledge_boundary import sanitize_public_result

#: Keys whose value names a collection; rewritten wholesale to the source name.
_COLLECTION_KEYS = frozenset({"collection", "source_collection", "target_collection"})


def sanitize_canary_result(value: Any, source_collection: str) -> Any:
    """Sanitize a canary hit without exposing its shadow target name."""
    target = f"{source_collection}{CANARY_SUFFIX}"
    sanitized = sanitize_public_result(value)

    def relabel(item: Any) -> Any:
        if isinstance(item, Mapping):
            relabeled: dict[str, Any] = {}
            for key, child in item.items():
                key_text = str(key)
                if key_text in _COLLECTION_KEYS:
                    relabeled[key_text] = source_collection
                else:
                    relabeled[key_text] = relabel(child)
            return relabeled
        if isinstance(item, list):
            return [relabel(child) for child in item]
        if isinstance(item, tuple):
            return tuple(relabel(child) for child in item)
        if isinstance(item, str):
            return item.replace(target, source_collection)
        return item

    return relabel(sanitized)


__all__ = ["sanitize_canary_result"]
