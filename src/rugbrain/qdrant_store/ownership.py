"""The write gate: does this repo own the corpus it is about to write to?

One job — decide, from a collection name. The facts it decides on are in
``owned_registry``; the lane-suffix strip is here because stripping suffixes IS
the gate's own concern (neither the ``_8b`` lane suffix nor the interim suffix
denotes a different owner).

Deliberately no ``is_owned``/``is_readonly`` helper: the gate is evaluated by
``store_write`` through the PACKAGE namespace, because that is the documented
test patch point for ``_RMI_RAG_QDRANT_WRITE_MODE`` and ``_INTERIM_SUFFIX``. A
helper that read the module-level imports would resolve to a different
namespace than the caller patched and silently ignore the patch — the exact
split-namespace hazard ``gate_collection_name``'s docstring warns about.
"""

from __future__ import annotations

from rugbrain.qdrant_store.lane_resolver import strip_twin_suffix


def gate_collection_name(
    collection: str | None,
    interim_suffix: str | None = None,
) -> str | None:
    """Canonical base name for the write gate, stripping BOTH suffixes.

    Ownership is a property of the corpus, not of the lane or the migration
    window: ``threat_intel_v2_8b<interim>``, ``threat_intel_v2_8b`` and
    ``threat_intel_v2`` are the same owned corpus, so all three must present
    ``threat_intel_v2`` to the gate. Returns None for a None name.

    *interim_suffix* is passed in rather than read from this module's global so
    the caller stays the single patch point: tests patch the PACKAGE attribute
    (``qdrant_store._INTERIM_SUFFIX``), and internal callers resolve shared
    names through the package namespace at call time. Reading the module global
    here would silently ignore those patches.
    """
    if not collection:
        return None
    base = collection
    if interim_suffix and base.endswith(interim_suffix):
        base = base[: -len(interim_suffix)]
    return strip_twin_suffix(base)


__all__ = ["gate_collection_name"]
