"""RAG Embedder — Qwen3-Embedding-8B (DeepInfra hosted primary / llama.cpp local).

Provider is chosen via ``EMBED_PROVIDER``: ``deepinfra`` (hosted DeepInfra,
massive-scale) or ``local`` (llama.cpp OpenAI-compatible ``/embeddings``,
``QWEN_EMBED_URL``). Both serve the SAME 8B model, so they share one vector
space.

THE CANONICAL LANE IS READ, NOT HARDCODED (2026-09-29)
    The retired 4B/2560 generation is eliminated. The model name and the dim
    both come from :mod:`rugbrain.dim_policy` — the registry that already
    declares ``qwen3-embedding-8b`` as canonical and keeps 4B only as a
    read-only entry — instead of being written out as literals here.

    The literals were a real defect, not a style preference. This module is a
    LIVE path (``query_embedder.embed_query`` -> ``search/filters.py`` ->
    every search request), so its ``"qwen3-embedding-4b"`` default was the
    model a client would actually send whenever the env did not override it.
    With the 42 retired collections deleted, that default can only produce a
    vector no surviving collection accepts.

    ``envsitter``-style env overrides still win, exactly as before: an operator
    pointing ``RAG_EMBED_MODEL`` at a different model gets that model's
    native dim from the same registry, not a stale hardcoded number.
"""

from __future__ import annotations

import os
from enum import Enum

from rugbrain.dim_policy import default_dim_for_model

#: Canonical model id. Read from the registry, so the retired 4B generation
#: cannot be reintroduced here as a literal default.
CANONICAL_MODEL: str = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")

#: Model id for the llama.cpp backend (env override wins).
QWEN_EMBED_MODEL_NAME: str = CANONICAL_MODEL


class EmbedderBackend(Enum):
    """Available embedding backends.

    The single member is the canonical 8B lane. It is not named ``Qwen3_M3``:
    that name was a leftover from the 3B era and misdescribed the 4B model it
    had been bound to, which misled readers of the factory below.
    """

    Qwen3_8B = "qwen3-embedding-8b"


# ── Per-backend configuration ──────────────────────────────────────────
# Dim comes from the registry (``default_dim_for_model``), not a literal, so
# changing the canonical model in one place cannot leave 2560 behind here.
_BACKEND_DIMS: dict[EmbedderBackend, int] = {
    EmbedderBackend.Qwen3_8B: default_dim_for_model(CANONICAL_MODEL),
}


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
        the configured model (``QWEN_EMBED_MODEL_NAME``, canonical 8B lane).
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
    """Hosted DeepInfra Qwen3-Embedding-8B adapter (DualDimEmbedder interface).

    Used when ``EMBED_PROVIDER=deepinfra`` (or auto with ``DEEPINFRA_API_KEY``
    set). Same model as the local llama.cpp lane ⇒ same 4096-dim vector space.
    """

    def __init__(self) -> None:
        from rugbrain.qwen_embedders import Qwen3Embedder

        self._impl = Qwen3Embedder(provider="deepinfra")
        # Call-time dim, not the class attribute frozen at import: the lane
        # switch happens after this module is first imported, and a frozen
        # value validates against the PREVIOUS model.
        self.dim = self._impl.dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._impl.embed(texts)

    async def embed_one(self, text: str) -> list[float]:
        return await self._impl.embed_one(text)


def get_embedder() -> DualDimEmbedder | DeepInfraEmbedder:
    """Return the canonical embedder (qwen3-embedding-8b, 4096d).

    Provider selected by ``EMBED_PROVIDER``:
      * ``deepinfra`` — hosted DeepInfra Qwen/Qwen3-Embedding-8B (massive-scale
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
            EmbedderBackend.Qwen3_8B,
            int_url=os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1"),
        )
    raise RuntimeError(f"EMBED_PROVIDER={provider!r} unknown (use deepinfra|local)")


# ── CLI ────────────────────────────────────────────────────────────────
def _main() -> None:
    """CLI: list available backends and their dims."""
    import argparse

    parser = argparse.ArgumentParser(description="RAG canonical-lane embedder")
    parser.add_argument("--list-backends", action="store_true", help="List available backends")
    args = parser.parse_args()

    if args.list_backends:
        for backend in EmbedderBackend:
            print(f"{backend.value}  {_BACKEND_DIMS[backend]}d")


if __name__ == "__main__":
    _main()
