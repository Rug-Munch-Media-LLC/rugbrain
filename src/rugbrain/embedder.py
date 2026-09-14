"""RAG Embedder — Qwen3-Embedding-4B (DeepInfra hosted primary / llama.cpp local).

Qwen3-Embedding-4B (2560d) is the primary embedding model. Provider is chosen
via ``EMBED_PROVIDER``: ``deepinfra`` (hosted DeepInfra, massive-scale) or
``local`` (llama.cpp OpenAI-compatible ``/embeddings``, ``QWEN_EMBED_URL``).
All Qdrant collections are 2560d Cosine.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any


class EmbedderBackend(Enum):
    """Available embedding backends."""

    Qwen3_M3 = "qwen3-embedding-4b"  # 2560d, canonical (local + DeepInfra)


# ── Per-backend configuration ──────────────────────────────────────────
_BACKEND_DIMS: dict[EmbedderBackend, int] = {
    EmbedderBackend.Qwen3_M3: 2560,
}

# Qwen3-Embedding-4B model id for the llama.cpp backend.
QWEN_EMBED_MODEL_NAME: str = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")


def backend_dim(backend: EmbedderBackend) -> int:
    """Return the embedding dimension for a backend."""
    return _BACKEND_DIMS[backend]


# ── Dual-dim wrapper ────────────────────────────────────────────────────
class DualDimEmbedder:
    """Wraps Qwen3 embedding for any backend. Same interface regardless of dim."""

    def __init__(
        self,
        backend: EmbedderBackend,
        int_url: str = "http://127.0.0.1:8004/v1",
    ) -> None:
        self.backend = backend
        self.int_url = int_url.rstrip("/")
        self.dim = backend_dim(backend)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per text.

        Uses the llama.cpp /embeddings endpoint (OpenAI-compatible) with
        the configured model (qwen3-embedding-4b).
        """
        import httpx

        if not texts:
            return []

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.int_url}/embeddings",
                json={"model": QWEN_EMBED_MODEL_NAME, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()

        # OpenAI-compatible response: {"data": [{"index": 0, "embedding": [...]}, ...]}
        items = data.get("data", [])
        vectors = [item["embedding"] for item in sorted(items, key=lambda i: i.get("index", 0))]

        # Validate dimensions to catch backend mismatches early.
        for i, vec in enumerate(vectors):
            if len(vec) != self.dim:
                raise RuntimeError(
                    f"Embedder {self.backend.value} returned {len(vec)}d vector "
                    f"for text[{i}], expected {self.dim}d. "
                    f"Check that llama.cpp has {QWEN_EMBED_MODEL_NAME} loaded."
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single text."""
        result = await self.embed([text])
        return result[0] if result else [0.0] * self.dim


# ── Factory ────────────────────────────────────────────────────────────
class DeepInfraEmbedder:
    """Hosted DeepInfra Qwen3-Embedding-4B adapter (DualDimEmbedder interface).

    Used when ``EMBED_PROVIDER=deepinfra`` (or auto with ``DEEPINFRA_API_KEY``
    set). Same model as the local llama.cpp lane ⇒ same 2560-dim vector space.
    """

    def __init__(self) -> None:
        from rugbrain.qwen_embedders import Qwen3Embedder

        self._impl = Qwen3Embedder(provider="deepinfra")
        self.dim = self._impl.DIMS

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._impl.embed(texts)

    async def embed_one(self, text: str) -> list[float]:
        return await self._impl.embed_one(text)


def get_embedder() -> DualDimEmbedder | DeepInfraEmbedder:
    """Return the canonical embedder (qwen3-embedding-4b, 2560d).

    Provider selected by ``EMBED_PROVIDER``:
      * ``deepinfra`` — hosted DeepInfra Qwen/Qwen3-Embedding-4B (massive-scale
        lane; requires DEEPINFRA_API_KEY).
      * ``local``     — llama.cpp (:8004, ``QWEN_EMBED_URL``) fallback.
      * unset         — auto: DeepInfra when ``DEEPINFRA_API_KEY`` is present,
        otherwise local llama.cpp.

    Async interface: ``await embedder.embed([texts])`` or ``await embedder.embed_one(text)``.
    """
    from rugbrain.qwen_embedders import DEEPINFRA_API_KEY

    provider = os.getenv("EMBED_PROVIDER", "").strip().lower()
    if not provider:
        provider = "deepinfra" if DEEPINFRA_API_KEY else "local"
    if provider == "deepinfra":
        return DeepInfraEmbedder()
    if provider == "local":
        return DualDimEmbedder(
            EmbedderBackend.Qwen3_M3,
            int_url=os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1"),
        )
    raise RuntimeError(f"EMBED_PROVIDER={provider!r} unknown (use deepinfra|local)")


# ── Migration helpers ──────────────────────────────────────────────────
async def reindex_collection(
    name: str,
    source: DualDimEmbedder,
    target: DualDimEmbedder,
    fetch_docs: Any,
    write_collection: Any,
    verify_queries: list[str] | None = None,
) -> bool:
    """Re-embed one collection from source backend to target backend.

    Args:
        name: collection name
        source: existing backend (e.g. qwen3-embedding-4b)
        target: new backend (e.g. qwen3-embedding:4b)
        fetch_docs: async () -> list[(id, text)] callable
        write_collection: async (name, vectors) -> None callable
        verify_queries: optional list of known queries to verify retrieval

    Returns True if migration succeeded (or verification skipped).
    """
    if source.dim == target.dim:
        # Same dim - no migration needed.
        return True

    docs = await fetch_docs()
    if not docs:
        return True

    texts = [text for _, text in docs]
    new_vectors = await target.embed(texts)

    new_name = f"{name}_v2"
    await write_collection(new_name, list(zip((id_ for id_, _ in docs), new_vectors, strict=False)))

    # Atomic swap - implementation-specific. Caller handles.
    # For FAISS: rename .index files. For Qdrant: rename collections.

    if verify_queries:
        # Verify retrieval on known queries before swapping production traffic.
        # Implementation-specific; placeholder for now.
        pass

    return True


# ── CLI ────────────────────────────────────────────────────────────────
def _main() -> None:
    """CLI: list available backends and their dims."""
    import argparse

    parser = argparse.ArgumentParser(description="RAG dual-dim embedder")
    parser.add_argument("--list-backends", action="store_true", help="List available backends")
    args = parser.parse_args()

    if args.list_backends:
        for backend in EmbedderBackend:
            print(f"{backend.value}  {_BACKEND_DIMS[backend]}d")


if __name__ == "__main__":
    _main()
