"""Dimension-split preflight -- embed a real probe, compare to the live store.

``dim_preflight`` exists so an embedder/lane mismatch fails LOUDLY instead of
silently returning zero results. A split between the serving dim and the store
dim is the difference between "this collection is empty" and "this retriever is
broken", and only one of those is a real answer.

Layout (split 2026-09-29 for the size ratchet):

* :mod:`dim_store`    -- parse a Qdrant collection-info payload for its width.
* :mod:`dim_probe`    -- the two grounded probes: live GET, and a real embed.
* :mod:`dim_verdict`  -- the (probe, store, declared) -> pass/fail decision.

This module keeps the orchestration plus the result type. ``embed_probe`` and
``fetch_collection_dim`` are re-exported as module attributes on purpose:
``preflight`` calls them as bare globals so the regression suite can patch
``dim_preflight.embed_probe`` and drive the whole verdict table without a live
Qdrant or a real embedding provider. Keep those names resolvable here.

READ-ONLY end to end. It must never PUT, POST, or DELETE against Qdrant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from rugbrain.eval.dim_probe import (
    DEFAULT_QDRANT_URL,
    PROBE_TEXT,
    embed_probe,
    fetch_collection_dim,
)
from rugbrain.eval.dim_store import extract_store_dim
from rugbrain.eval.dim_verdict import classify

# Re-exported so callers (and the regression suite) reach the probes through
# this module. Every name in __all__ is bound by an import statement above.
__all__ = [
    "DEFAULT_QDRANT_URL",
    "PROBE_TEXT",
    "DimCheck",
    "collections_from_env",
    "declared_dim",
    "embed_probe",
    "extract_store_dim",
    "fetch_collection_dim",
    "preflight",
    "split_collections",
]


@dataclass(frozen=True)
class DimCheck:
    """Outcome of comparing one embedder probe against one live collection."""

    collection: str
    probe_dim: int
    store_dim: int
    store_dim_source: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "probe_dim": self.probe_dim,
            "store_dim": self.store_dim,
            "store_dim_source": self.store_dim_source,
            "ok": self.ok,
            "detail": self.detail,
        }


def declared_dim(collection: str) -> int | None:
    """The width this repo DECLARES it writes ``collection`` at.

    This is the correct counterpart to the embedder probe, and using the probe
    alone is what produced a false dimension split on every pinned collection.

    One embedder emits one global width (4096 on the canonical lane). Six
    collections are DELIBERATELY pinned narrower -- ``coding_knowledge``,
    ``public_crypto_patterns``, ``rug_events``, ``solana_patterns`` at 2560 and
    ``wallet_clusters``/``youtube_videos`` at 1024. A preflight that compares
    the 4096 probe against those declares a split that does not exist in
    production.

    So the question is not "does one global probe equal every width" -- it never
    does, by design. The question is: for THIS collection, does what the
    embedder produces match what the store holds AND what the repo declares it
    writes? A declared dim is the only per-collection ground truth available
    without embedding per collection, which would multiply DeepInfra cost for
    no extra signal.
    """
    from rugbrain.qdrant_store.lane_resolver import declared_lane_dim

    return declared_lane_dim(collection)


async def preflight(
    collections: list[str],
    qdrant_url: str = DEFAULT_QDRANT_URL,
    probe_text: str = PROBE_TEXT,
) -> list[DimCheck]:
    """Compare the live embedder's output width to each collection's width.

    One probe is embedded and reused for every collection: the embedder's
    output width is a property of the embedder, not of the collection, and
    re-embedding per collection would multiply cost for no extra signal.

    The probe is compared against the collection's DECLARED width, not against
    the probe alone -- see :func:`declared_dim`. A collection that declares a
    narrower pinned width is correct, and flagging it is a false split.
    """
    probe_dim, backend = await embed_probe(probe_text)
    checks: list[DimCheck] = []
    for collection in collections:
        store_dim, source = await fetch_collection_dim(collection, qdrant_url)
        ok, detail = classify(
            collection=collection,
            probe_dim=probe_dim,
            backend=backend,
            store_dim=store_dim,
            source=source,
            declared=declared_dim(collection),
        )
        checks.append(
            DimCheck(
                collection=collection,
                probe_dim=probe_dim,
                store_dim=store_dim,
                store_dim_source=source,
                ok=ok,
                detail=detail,
            )
        )
    return checks


def split_collections(checks: list[DimCheck]) -> list[DimCheck]:
    """The subset that is split. Empty means serving and store agree."""
    return [c for c in checks if not c.ok]


def collections_from_env(default: list[str]) -> list[str]:
    """Read the collection list from ``EVAL_DIM_COLLECTIONS``.

    Empty string means "use *default*". Never a limit: passing an empty list
    to a gate is how a monitor ends up measuring nothing and reporting green.
    """
    raw = os.environ.get("EVAL_DIM_COLLECTIONS", "").strip()
    if not raw:
        return list(default)
    return [c.strip() for c in raw.split(",") if c.strip()]
