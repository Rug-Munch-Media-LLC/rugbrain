"""RAG Embedder — Qwen3-Embedding-4B primary (llama.cpp).

Qwen3-Embedding-4B (2560d) is the primary embedding model, served by llama.cpp's
OpenAI-compatible ``/embeddings`` endpoint (port 8085/8086).
bge-m3 (1024d) is retired. All Qdrant collections are 2560d Cosine.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any


class EmbedderBackend(Enum):
    """Available embedding backends."""

    BGE_M3 = "bge-m3"  # 1024d, current primary (lightweight, CPU-friendly)
    QWEN3_4B = "qwen3-embed:4b"  # 2560d, legacy (retired)


# ── Per-backend configuration ──────────────────────────────────────────
_BACKEND_DIMS: dict[EmbedderBackend, int] = {
    EmbedderBackend.BGE_M3: 1024,
    EmbedderBackend.QWEN3_4B: 2560,
}

# Infinity model id for the BGE-M3 backend.
INFINITY_MODEL: str = os.getenv("RAG_INFINITY_EMBED_MODEL", "BAAI/bge-m3")


def backend_dim(backend: EmbedderBackend) -> int:
    """Return the embedding dimension for a backend."""
    return _BACKEND_DIMS[backend]


# ── Dual-dim wrapper ────────────────────────────────────────────────────
class DualDimEmbedder:
    """Wraps Infinity embedding for any backend. Same interface regardless of dim."""

    def __init__(
        self,
        backend: EmbedderBackend,
        infinity_url: str = "http://infinity-rag:8001",
    ) -> None:
        self.backend = backend
        self.infinity_url = infinity_url.rstrip("/")
        self.dim = backend_dim(backend)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per text.

        Uses the Infinity /embeddings endpoint (OpenAI-compatible) with
        the configured model (BAAI/bge-m3).
        """
        import httpx

        if not texts:
            return []

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.infinity_url}/embeddings",
                json={"model": INFINITY_MODEL, "input": texts},
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
                    f"Check that Infinity has {INFINITY_MODEL} loaded."
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single text."""
        result = await self.embed([text])
        return result[0] if result else [0.0] * self.dim


# ── Factory ────────────────────────────────────────────────────────────
def get_embedder() -> DualDimEmbedder:
    """Return the canonical embedder (bge-m3, 1024d, via Infinity).

    Async interface: ``await embedder.embed([texts])`` or ``await embedder.embed_one(text)``.
    """
    return DualDimEmbedder(
        EmbedderBackend.BGE_M3,
        infinity_url=os.getenv("INFINITY_URL", "http://infinity-rag:8001"),
    )


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
        source: existing backend (e.g. bge-m3)
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
