"""Dimension-split preflight — embeds a real probe, compares to the live store.

Why this exists
---------------
The fleet spent a full session with the environment naming Qwen3-Embedding-8B
at 4096-dim while the Qdrant collection was 2560-wide. Nothing caught it:

  * ``tests/test_embed_dim_startup.py`` injects ``SimpleNamespace`` fakes and
    makes ZERO live Qdrant calls, so it asserts against its own assumptions.
  * The env is a *declaration*. The collection width is a *fact*. A guard that
    reads only the declaration is a tautology.

This preflight closes that gap by grounding both sides:

  probe side   — embed a real string through the real embedder, then read
                 ``len(vector)``. Whatever the provider actually returns.
  store side   — GET ``http://localhost:6334/collections/{c}`` and read the
                 configured vector width. Whatever the store actually holds.

If they disagree, serving dim and store dim have split and every query against
that collection is either refused or silently mis-scored.

READ-ONLY. This module issues GET against Qdrant and nothing else. It must
never PUT, POST, or DELETE — an eval that mutates the store it measures is not
an eval.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_QDRANT_URL = "http://localhost:6334"

# The probe text is arbitrary but non-empty: some providers route short or
# punctuation-only input down a different path than a real sentence.
PROBE_TEXT = "dimension preflight probe: qwen3-embedding dimension check"


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


def extract_store_dim(info: dict[str, Any]) -> tuple[int, str]:
    """Read the configured vector width out of a Qdrant collection info.

    Handles all three shapes: named vectors (a dict of name → config),
    unnamed vectors (a bare config dict), and an absent config (0 = unknown).
    Returns ``(width, how_it_was_read)`` so a failure report can say which
    shape it saw rather than just "mismatch".
    """
    params = (info.get("config") or {}).get("params") or {}
    vectors = params.get("vectors")
    # Legacy unnamed shape: vectors={"size": N, "distance": ...}. Checked
    # FIRST because it is a dict too — treating it as a name->config map
    # yields an empty set and would report a false split on every
    # pre-migration collection.
    if isinstance(vectors, dict) and isinstance(vectors.get("size"), int):
        return vectors["size"], "config.params.vectors.size (unnamed)"
    if isinstance(vectors, dict):
        sizes = {
            name: (cfg or {}).get("size") for name, cfg in vectors.items() if isinstance(cfg, dict)
        }
        sizes = {n: s for n, s in sizes.items() if isinstance(s, int)}
        if not sizes:
            return 0, "config.params.vectors: no integer size"
        if len(sizes) == 1:
            name, size = next(iter(sizes.items()))
            return size, f"config.params.vectors[{name}].size"
        widths = sorted(set(sizes.values()))
        if len(widths) == 1:
            return widths[0], f"config.params.vectors: all {len(sizes)} named vectors"
        return 0, f"config.params.vectors: MIXED widths {sizes}"
    if isinstance(vectors, int):
        return vectors, "config.params.vectors (legacy int)"
    return 0, "config.params.vectors: absent"


async def fetch_collection_dim(
    collection: str, qdrant_url: str = DEFAULT_QDRANT_URL
) -> tuple[int, str]:
    """GET one collection's configured width. Read-only; never mutates."""
    import httpx

    url = f"{qdrant_url.rstrip('/')}/collections/{collection}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        return 0, f"GET {url} -> HTTP {resp.status_code}"
    body = resp.json()
    if body.get("status") != "ok":
        return 0, f"GET {url} -> status={body.get('status')!r}"
    return extract_store_dim(body.get("result") or {})


async def embed_probe(text: str = PROBE_TEXT) -> tuple[int, str]:
    """Embed a real probe and return ``(width, which_backend)``.

    Imports the embedder lazily and inside the coroutine so that an unrelated
    import failure elsewhere in the package cannot kill the preflight at module
    import time — the same decoupling that keeps the eval out of the graph layer.
    """
    from rugbrain.embeddings import current_backend, get_query_embedding

    vector = await get_query_embedding(text)
    return len(vector), current_backend()


def declared_dim(collection: str) -> int | None:
    """The width this repo DECLARES it writes ``collection`` at.

    This is the correct counterpart to the embedder probe, and using the probe
    alone is what produced a false dimension split on every pinned collection.

    One embedder emits one global width (4096 on the canonical lane). Six
    collections are DELIBERATELY pinned narrower — ``coding_knowledge``,
    ``public_crypto_patterns``, ``rug_events``, ``solana_patterns`` at 2560 and
    ``wallet_clusters``/``youtube_videos`` at 1024. A preflight that compares
    the 4096 probe against those declares a split that does not exist in
    production, and a monitor that cries wolf on the pinned set is a monitor
    people learn to disable.

    So the question is not "does one global probe equal every width" — it never
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
    the probe alone — see :func:`declared_dim`. A collection that declares a
    narrower pinned width is correct, and flagging it is a false split.
    """
    probe_dim, backend = await embed_probe(probe_text)
    checks: list[DimCheck] = []
    for collection in collections:
        store_dim, source = await fetch_collection_dim(collection, qdrant_url)
        declared = declared_dim(collection)
        if store_dim == 0:
            ok = False
            detail = f"could not read store width for {collection!r}: {source}"
        elif declared is None:
            # No declared fact and no usable probe comparison: the store width
            # stands alone. That is genuinely unknown, so it is not a pass and
            # not a false alarm — it is reported, and the caller decides.
            ok = probe_dim == store_dim
            detail = (
                f"unclassified collection: probe {probe_dim} vs store {store_dim} "
                f"({backend}); {source}"
            )
        elif declared == store_dim and declared != probe_dim:
            # Deliberate narrow pin. The store matches the declaration, and the
            # difference from the global probe is the DESIGN, not a split.
            ok = True
            detail = (
                f"pinned by design: declared {declared} == store {store_dim} "
                f"({source}); global probe is {probe_dim} ({backend})"
            )
        elif declared != store_dim:
            ok = False
            detail = (
                f"DIMENSION SPLIT: repo declares {declared} for {collection!r} "
                f"but store is {store_dim}-wide ({source})"
            )
        elif probe_dim != store_dim:
            ok = False
            detail = (
                f"DIMENSION SPLIT: embedder returned {probe_dim} "
                f"(backend={backend}) but {collection!r} is {store_dim}-wide "
                f"({source}). Queries will be refused or mis-scored."
            )
        else:
            ok = True
            detail = f"probe {probe_dim} == store {store_dim} == declared ({backend})"
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


__all__ = [
    "DEFAULT_QDRANT_URL",
    "DimCheck",
    "PROBE_TEXT",
    "collections_from_env",
    "declared_dim",
    "embed_probe",
    "extract_store_dim",
    "fetch_collection_dim",
    "preflight",
    "split_collections",
]
