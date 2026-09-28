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


async def preflight(
    collections: list[str],
    qdrant_url: str = DEFAULT_QDRANT_URL,
    probe_text: str = PROBE_TEXT,
) -> list[DimCheck]:
    """Compare the live embedder's output width to each collection's width.

    One probe is embedded and reused for every collection: the embedder's
    output width is a property of the embedder, not of the collection, and
    re-embedding per collection would multiply cost for no extra signal.
    """
    probe_dim, backend = await embed_probe(probe_text)
    checks: list[DimCheck] = []
    for collection in collections:
        store_dim, source = await fetch_collection_dim(collection, qdrant_url)
        if store_dim == 0:
            ok = False
            detail = f"could not read store width for {collection!r}: {source}"
        elif probe_dim == store_dim:
            ok = True
            detail = f"probe {probe_dim} == store {store_dim} ({backend})"
        else:
            ok = False
            detail = (
                f"DIMENSION SPLIT: embedder returned {probe_dim} "
                f"(backend={backend}) but {collection!r} is {store_dim}-wide "
                f"({source}). Queries will be refused or mis-scored."
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


__all__ = [
    "DEFAULT_QDRANT_URL",
    "DimCheck",
    "PROBE_TEXT",
    "collections_from_env",
    "embed_probe",
    "extract_store_dim",
    "fetch_collection_dim",
    "preflight",
    "split_collections",
]
