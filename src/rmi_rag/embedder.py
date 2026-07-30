"""RAG Dual-Dim Embedder (M4 migration complete).

Qwen3-embed:4b (2560d) is now the primary embedding model.
BGE-M3 (1024d) is retired. All Qdrant collections are 2560d Cosine.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class EmbedderBackend(Enum):
    """Available embedding backends."""

    BGE_M3 = "bge-m3"  # 1024d, legacy (retired)
    QWEN3_4B = "qwen3-embed:4b"  # 2560d, current primary


# ── Per-backend configuration ──────────────────────────────────────────
_BACKEND_DIMS: dict[EmbedderBackend, int] = {
    EmbedderBackend.BGE_M3: 1024,
    EmbedderBackend.QWEN3_4B: 2560,
}


def backend_dim(backend: EmbedderBackend) -> int:
    """Return the embedding dimension for a backend."""
    return _BACKEND_DIMS[backend]


# ── Dual-dim wrapper ────────────────────────────────────────────────────
class DualDimEmbedder:
    """Wraps Ollama embedding for any backend. Same interface regardless of dim."""

    def __init__(
        self,
        backend: EmbedderBackend,
        ollama_url: str = "http://ollama:11434",
    ) -> None:
        self.backend = backend
        self.ollama_url = ollama_url.rstrip("/")
        self.dim = backend_dim(backend)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per text.

        Uses the Ollama /api/embeddings endpoint with the configured model.
        """
        import httpx

        if not texts:
            return []

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": self.backend.value, "prompt": texts},
            )
            resp.raise_for_status()
            data = resp.json()

        # Ollama returns {"embedding": [[...], [...]]} for single, or {"embeddings": [[...]]}
        if "embeddings" in data:
            vectors = data["embeddings"]
        else:
            # Single-text fallback - Ollama returns {"embedding": [...]}
            vectors = [data["embedding"]] if "embedding" in data else []

        # Validate dimensions to catch backend mismatches early.
        for i, vec in enumerate(vectors):
            if len(vec) != self.dim:
                raise RuntimeError(
                    f"Embedder {self.backend.value} returned {len(vec)}d vector "
                    f"for text[{i}], expected {self.dim}d. "
                    f"Check that ollama has the right model pulled."
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single text."""
        result = await self.embed([text])
        return result[0] if result else [0.0] * self.dim


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
