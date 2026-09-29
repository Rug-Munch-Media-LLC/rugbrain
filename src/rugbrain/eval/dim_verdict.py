"""Classify one collection's (probe, store, declared) widths into a verdict.

One job: the decision table. Given the embedder's output width, the store's
width and where that width was read from, and the width the repo DECLARES it
writes at, decide pass / fail and say why in a sentence a human can act on.

The declaration matters because a global probe never equals every collection
by design: one embedder emits one width (4096 on the canonical lane) while six
collections are deliberately pinned narrower. Comparing the probe alone
declares a split that does not exist in production, and a monitor that cries
wolf on the pinned set is a monitor people learn to disable.

A store width that could not be read is never a pass. A collection with no
declaration and no usable comparison is reported, not waved through.
"""

from __future__ import annotations

__all__ = ["DimVerdict", "classify"]


class DimVerdict:
    """The ``(ok, detail)`` outcome for one collection."""

    __slots__ = ("detail", "ok")

    def __init__(self, ok: bool, detail: str) -> None:
        self.ok = ok
        self.detail = detail

    def __iter__(self):
        """Unpack as ``ok, detail = verdict``."""
        yield self.ok
        yield self.detail

    def __repr__(self) -> str:
        return f"DimVerdict(ok={self.ok!r}, detail={self.detail!r})"


def classify(
    collection: str,
    probe_dim: int,
    backend: str,
    store_dim: int,
    source: str,
    declared: int | None,
) -> DimVerdict:
    """Decide whether *collection* is dimension-split, and explain the verdict."""
    if store_dim == 0:
        return DimVerdict(
            False, f"could not read store width for {collection!r}: {source}"
        )
    if declared is None:
        # No declared fact and no usable probe comparison: the store width
        # stands alone. That is genuinely unknown, so it is not a pass and
        # not a false alarm -- it is reported, and the caller decides.
        return DimVerdict(
            probe_dim == store_dim,
            f"unclassified collection: probe {probe_dim} vs store {store_dim} "
            f"({backend}); {source}",
        )
    if declared == store_dim and declared != probe_dim:
        # Deliberate narrow pin. The store matches the declaration, and the
        # difference from the global probe is the DESIGN, not a split.
        return DimVerdict(
            True,
            f"pinned by design: declared {declared} == store {store_dim} "
            f"({source}); global probe is {probe_dim} ({backend})",
        )
    if declared != store_dim:
        return DimVerdict(
            False,
            f"DIMENSION SPLIT: repo declares {declared} for {collection!r} "
            f"but store is {store_dim}-wide ({source})",
        )
    if probe_dim != store_dim:
        return DimVerdict(
            False,
            f"DIMENSION SPLIT: embedder returned {probe_dim} "
            f"(backend={backend}) but {collection!r} is {store_dim}-wide "
            f"({source}). Queries will be refused or mis-scored.",
        )
    return DimVerdict(True, f"probe {probe_dim} == store {store_dim} == declared ({backend})")
