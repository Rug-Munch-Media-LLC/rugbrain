"""The two probes a dimension preflight compares: the live store and the embedder.

Store side -- GET ``/collections/{c}`` and read the configured vector width.
Probe side -- embed a real string through the real embedder and read
``len(vector)``. Whatever each side actually returns, not what it declares.

Why it exists
-------------
The fleet spent a full session with the environment naming Qwen3-Embedding-8B
at 4096-dim while the Qdrant collection was 2560-wide, and nothing caught it.
The env is a *declaration*; the collection width is a *fact*, and a guard that
reads only the declaration is a tautology. Grounding both sides is the whole
point of this module.

If they disagree, serving dim and store dim have split and every query against
that collection is either refused or silently mis-scored.

READ-ONLY. This module issues GET against Qdrant and nothing else. It must
never PUT, POST, or DELETE.
"""

from __future__ import annotations

DEFAULT_QDRANT_URL = "http://localhost:6334"

# The probe text is arbitrary but non-empty: some providers route short or
# punctuation-only input down a different path than a real sentence.
PROBE_TEXT = "dimension preflight probe: qwen3-embedding dimension check"

__all__ = ["DEFAULT_QDRANT_URL", "PROBE_TEXT", "embed_probe", "fetch_collection_dim"]


async def fetch_collection_dim(
    collection: str, qdrant_url: str = DEFAULT_QDRANT_URL
) -> tuple[int, str]:
    """GET one collection's configured width. Read-only; never mutates."""
    import httpx

    from rugbrain.eval.dim_store import extract_store_dim

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
    import time -- the same decoupling that keeps the eval out of the graph
    layer.
    """
    from rugbrain.embeddings import current_backend, get_query_embedding

    vector = await get_query_embedding(text)
    return len(vector), current_backend()
